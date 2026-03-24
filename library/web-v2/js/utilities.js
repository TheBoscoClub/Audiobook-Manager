/**
 * The Back Office - Library Utilities
 * JavaScript for database management, metadata editing, duplicates, and bulk operations
 */

// Use empty string for API_BASE since fetch URLs already include /api/ prefix
// This allows the proxy server to properly route requests
const API_BASE = "";

// ============================================
// Safe Fetch Wrapper - Always checks response.ok
// ============================================

/**
 * Fetch wrapper that always checks response.ok before parsing JSON.
 * Throws a detailed error for non-2xx responses.
 *
 * @param {string} url - The URL to fetch
 * @param {object} options - Fetch options (method, headers, body, etc.)
 * @returns {Promise<object>} - Parsed JSON response
 * @throws {Error} - If response is not ok or JSON parsing fails
 */
async function safeFetch(url, options = {}) {
  const response = await fetch(url, options);

  if (!response.ok) {
    // Try to get error message from response body
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData = await response.json();
      if (errorData.error) {
        errorMessage = errorData.error;
      } else if (errorData.message) {
        errorMessage = errorData.message;
      }
    } catch (e) {
      // Response wasn't JSON, use default error message
    }
    throw new Error(errorMessage);
  }

  return response.json();
}

// State
let currentSection = "database";
let editingAudiobook = null;
let duplicatesData = [];
let bulkSelection = new Set();
let duplicateSelection = new Set();
let activeOperationId = null;
let pollingInterval = null;

// Initialize
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initDatabaseSection();
  initConversionSection();
  initAudiobooksSection();
  initDuplicatesSection();
  initBulkSection();
  initActivitySection();
  initSystemSection();
  initUsersSection();
  initModals();
  initOperationStatus();

  // Load initial stats
  loadDatabaseStats();

  // Check for any active operations on page load
  checkActiveOperations();
});

// ============================================
// Tab Navigation
// ============================================

function initTabs() {
  document.querySelectorAll(".cabinet-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const section = tab.dataset.section;
      switchSection(section);
    });
  });
}

function switchSection(section) {
  // Update tabs
  document.querySelectorAll(".cabinet-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.section === section);
  });

  // Update content
  document.querySelectorAll(".drawer-content").forEach((content) => {
    content.classList.toggle("active", content.id === `${section}-section`);
  });

  currentSection = section;
}

// ============================================
// Database Management
// ============================================

function initDatabaseSection() {
  document
    .getElementById("refresh-stats")
    ?.addEventListener("click", loadDatabaseStats);
  document
    .getElementById("add-new-audiobooks")
    ?.addEventListener("click", addNewAudiobooks);
  document
    .getElementById("rescan-library")
    ?.addEventListener("click", rescanLibraryAsync);
  document
    .getElementById("reimport-db")
    ?.addEventListener("click", reimportDatabaseAsync);
  document
    .getElementById("generate-hashes")
    ?.addEventListener("click", generateHashesAsync);
  document
    .getElementById("generate-checksums")
    ?.addEventListener("click", generateChecksumsAsync);
  document
    .getElementById("vacuum-db")
    ?.addEventListener("click", vacuumDatabase);
  document
    .getElementById("export-db")
    ?.addEventListener("click", exportDatabase);
  document.getElementById("export-json")?.addEventListener("click", exportJson);
  document.getElementById("export-csv")?.addEventListener("click", exportCsv);
}

async function loadDatabaseStats() {
  try {
    // Fetch stats from API using safeFetch for proper error handling
    const [stats, hashStats] = await Promise.all([
      safeFetch(`${API_BASE}/api/stats`),
      safeFetch(`${API_BASE}/api/hash-stats`),
    ]);

    // Update UI - map API field names to display elements
    document.getElementById("db-total-books").textContent =
      stats.total_audiobooks?.toLocaleString() || "-";
    document.getElementById("db-total-hours").textContent = stats.total_hours
      ? `${Math.round(stats.total_hours).toLocaleString()} hrs`
      : "-";
    document.getElementById("db-total-size").textContent = stats.total_size_gb
      ? `${stats.total_size_gb.toFixed(1)} GB`
      : "-";
    document.getElementById("db-total-authors").textContent =
      stats.unique_authors?.toLocaleString() || "-";
    document.getElementById("db-total-narrators").textContent =
      stats.unique_narrators?.toLocaleString() || "-";
    document.getElementById("db-hash-count").textContent =
      hashStats.hashed_count !== undefined
        ? `${hashStats.hashed_count} / ${hashStats.total_audiobooks}`
        : "-";
    document.getElementById("db-duplicate-groups").textContent =
      hashStats.duplicate_groups !== undefined
        ? hashStats.duplicate_groups
        : "-";
    document.getElementById("db-file-size").textContent = stats.database_size_mb
      ? `${stats.database_size_mb.toFixed(1)} MB`
      : "-";
  } catch (error) {
    console.error("Failed to load stats:", error);
    showToast("Failed to load database statistics", "error");
  }
}

async function rescanLibrary() {
  showProgress(
    "Scanning Library",
    "Scanning audiobook directory for new files...",
  );
  try {
    const result = await safeFetch(`${API_BASE}/api/utilities/rescan`, {
      method: "POST",
    });
    hideProgress();

    if (result.success) {
      showToast(`Scan complete: ${result.files_found} files found`, "success");
      loadDatabaseStats();
    } else {
      showToast(result.error || "Scan failed", "error");
    }
  } catch (error) {
    hideProgress();
    showToast("Failed to start scan: " + error.message, "error");
  }
}

async function reimportDatabase() {
  if (
    !(await confirmAction(
      "Reimport Database",
      "This will rebuild the database from scan results. Existing narrator and genre data will be preserved. Continue?",
    ))
  ) {
    return;
  }

  showProgress("Reimporting Database", "Importing audiobooks to database...");
  try {
    const res = await fetch(`${API_BASE}/api/utilities/reimport`, {
      method: "POST",
    });
    const result = await res.json();
    hideProgress();

    if (result.success) {
      showToast(
        `Import complete: ${result.imported_count} audiobooks`,
        "success",
      );
      loadDatabaseStats();
    } else {
      showToast(result.error || "Import failed", "error");
    }
  } catch (error) {
    hideProgress();
    showToast("Failed to reimport: " + error.message, "error");
  }
}

async function generateHashes() {
  showProgress(
    "Generating Hashes",
    "Calculating SHA-256 hashes for all audiobooks...",
  );
  try {
    const res = await fetch(`${API_BASE}/api/utilities/generate-hashes`, {
      method: "POST",
    });
    const result = await res.json();
    hideProgress();

    if (result.success) {
      showToast(`Generated ${result.hashes_generated} hashes`, "success");
      loadDatabaseStats();
    } else {
      showToast(result.error || "Hash generation failed", "error");
    }
  } catch (error) {
    hideProgress();
    showToast("Failed to generate hashes: " + error.message, "error");
  }
}

async function vacuumDatabase() {
  showProgress(
    "Vacuuming Database",
    "Optimizing database and reclaiming space...",
  );
  try {
    const res = await fetch(`${API_BASE}/api/utilities/vacuum`, {
      method: "POST",
    });
    const result = await res.json();
    hideProgress();

    if (result.success) {
      showToast(
        `Database vacuumed. Space reclaimed: ${result.space_reclaimed_mb?.toFixed(1) || "?"} MB`,
        "success",
      );
      loadDatabaseStats();
    } else {
      showToast(result.error || "Vacuum failed", "error");
    }
  } catch (error) {
    hideProgress();
    showToast("Failed to vacuum database: " + error.message, "error");
  }
}

function exportDatabase() {
  window.location.href = `${API_BASE}/api/utilities/export-db`;
}

function exportJson() {
  window.location.href = `${API_BASE}/api/utilities/export-json`;
}

function exportCsv() {
  window.location.href = `${API_BASE}/api/utilities/export-csv`;
}

// ============================================
// Audiobook Management (Edit/Delete)
// ============================================

function initAudiobooksSection() {
  const searchInput = document.getElementById("edit-search");
  const searchBtn = document.getElementById("edit-search-btn");

  searchBtn?.addEventListener("click", () => searchForEdit(searchInput.value));
  searchInput?.addEventListener("keypress", (e) => {
    if (e.key === "Enter") searchForEdit(searchInput.value);
  });

  document
    .getElementById("close-edit-form")
    ?.addEventListener("click", closeEditForm);
  document
    .getElementById("cancel-edit")
    ?.addEventListener("click", closeEditForm);
  document
    .getElementById("edit-audiobook-form")
    ?.addEventListener("submit", saveAudiobook);
  document
    .getElementById("delete-audiobook")
    ?.addEventListener("click", deleteAudiobook);
}

async function searchForEdit(query) {
  if (!query.trim()) return;

  const resultsContainer = document.getElementById("edit-search-results");
  resultsContainer.innerHTML = '<p class="placeholder-text">Searching...</p>';

  try {
    const res = await fetch(
      `${API_BASE}/api/audiobooks?search=${encodeURIComponent(query)}&per_page=20`,
    );
    const data = await res.json();

    if (data.audiobooks?.length > 0) {
      resultsContainer.innerHTML = data.audiobooks
        .map(
          (book) => `
                <div class="search-result-item" data-id="${book.id}">
                    <img src="${book.cover_url || "/api/covers/default.jpg"}"
                         alt="" class="result-cover"
                         onerror="this.src='/api/covers/default.jpg'">
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(book.title)}</div>
                        <div class="result-meta">${escapeHtml(book.author)} | ${escapeHtml(book.narrator || "Unknown narrator")}</div>
                    </div>
                </div>
            `,
        )
        .join("");

      // Add click handlers
      resultsContainer
        .querySelectorAll(".search-result-item")
        .forEach((item) => {
          item.addEventListener("click", () =>
            loadAudiobookForEdit(item.dataset.id),
          );
        });
    } else {
      resultsContainer.innerHTML =
        '<p class="placeholder-text">No audiobooks found</p>';
    }
  } catch (error) {
    resultsContainer.innerHTML =
      '<p class="placeholder-text">Search failed</p>';
    showToast("Search failed: " + error.message, "error");
  }
}

async function loadAudiobookForEdit(id) {
  try {
    const res = await fetch(`${API_BASE}/api/audiobooks/${id}`);
    const book = await res.json();

    editingAudiobook = book;

    // Populate form
    document.getElementById("edit-id").value = book.id;
    document.getElementById("edit-title").value = book.title || "";
    document.getElementById("edit-author").value = book.author || "";
    document.getElementById("edit-narrator").value = book.narrator || "";
    document.getElementById("edit-series").value = book.series || "";
    document.getElementById("edit-series-seq").value =
      book.series_sequence || "";
    document.getElementById("edit-publisher").value = book.publisher || "";
    document.getElementById("edit-year").value = book.published_year || "";
    document.getElementById("edit-asin").value = book.asin || "";
    document.getElementById("edit-file-path").textContent =
      book.file_path || "-";

    // Show form
    document.getElementById("edit-form-container").style.display = "block";
    document
      .getElementById("edit-form-container")
      .scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    showToast("Failed to load audiobook: " + error.message, "error");
  }
}

function closeEditForm() {
  document.getElementById("edit-form-container").style.display = "none";
  editingAudiobook = null;
}

async function saveAudiobook(e) {
  e.preventDefault();

  const id = document.getElementById("edit-id").value;
  const data = {
    title: document.getElementById("edit-title").value,
    author: document.getElementById("edit-author").value,
    narrator: document.getElementById("edit-narrator").value,
    series: document.getElementById("edit-series").value || null,
    series_sequence: document.getElementById("edit-series-seq").value || null,
    publisher: document.getElementById("edit-publisher").value || null,
    published_year: document.getElementById("edit-year").value || null,
    asin: document.getElementById("edit-asin").value || null,
  };

  try {
    const res = await fetch(`${API_BASE}/api/audiobooks/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });

    const result = await res.json();

    if (result.success) {
      showToast("Audiobook updated successfully", "success");
      closeEditForm();
      // Refresh search results
      const query = document.getElementById("edit-search").value;
      if (query) searchForEdit(query);
    } else {
      showToast(result.error || "Update failed", "error");
    }
  } catch (error) {
    showToast("Failed to save: " + error.message, "error");
  }
}

async function deleteAudiobook() {
  if (!editingAudiobook) return;

  const confirmed = await confirmAction(
    "Delete Audiobook",
    `Are you sure you want to delete "${editingAudiobook.title}"?\n\nThis will remove it from the database. The audio file will NOT be deleted.`,
  );

  if (!confirmed) return;

  try {
    const res = await fetch(
      `${API_BASE}/api/audiobooks/${editingAudiobook.id}`,
      {
        method: "DELETE",
      },
    );

    const result = await res.json();

    if (result.success) {
      showToast("Audiobook deleted from database", "success");
      closeEditForm();
      document.getElementById("edit-search-results").innerHTML =
        '<p class="placeholder-text">Enter a search term to find audiobooks</p>';
      loadDatabaseStats();
    } else {
      showToast(result.error || "Delete failed", "error");
    }
  } catch (error) {
    showToast("Failed to delete: " + error.message, "error");
  }
}

// ============================================
// Duplicates Section
// ============================================

function initDuplicatesSection() {
  document
    .getElementById("find-duplicates")
    ?.addEventListener("click", findDuplicates);
  document
    .getElementById("select-all-dups")
    ?.addEventListener("click", selectAllDuplicates);
  document
    .getElementById("deselect-all-dups")
    ?.addEventListener("click", deselectAllDuplicates);
  document
    .getElementById("delete-selected-dups")
    ?.addEventListener("click", deleteSelectedDuplicates);
}

// Track current duplicate detection mode
let currentDupMode = "title";
// Track selected paths for checksum-based deletions
let checksumPathSelection = new Set();

// Endpoint mapping for duplicate detection methods
const DUPLICATE_ENDPOINTS = {
  hash: "/api/duplicates",
  "source-checksum": "/api/duplicates/by-checksum?type=sources",
  "library-checksum": "/api/duplicates/by-checksum?type=library",
  title: "/api/duplicates/by-title",
};

/**
 * Show placeholder message in duplicates list
 */
function showDuplicatesPlaceholder(container, message) {
  container.textContent = "";
  const p = document.createElement("p");
  p.className = "placeholder-text";
  p.textContent = message;
  container.appendChild(p);
}

/**
 * Handle checksum-based duplicate response
 */
function handleChecksumDuplicates(data, method, container) {
  const checksumType = method === "source-checksum" ? "sources" : "library";
  const checksumData = data[checksumType];

  if (!checksumData || !checksumData.exists) {
    showDuplicatesPlaceholder(
      container,
      checksumData?.error ||
        "Checksum index not found. Generate checksums first from Database section.",
    );
    document.getElementById("dup-actions").style.display = "none";
    return;
  }

  duplicatesData = checksumData.duplicate_groups || [];
  duplicateSelection.clear();
  checksumPathSelection.clear();
  document.getElementById("dup-group-count").textContent =
    duplicatesData.length;

  if (duplicatesData.length > 0) {
    renderChecksumDuplicates(checksumType);
    document.getElementById("dup-actions").style.display = "flex";
    document.getElementById("selected-count").textContent = "0";
    document.getElementById("delete-selected-dups").disabled = true;
    showToast(
      `Found ${checksumData.total_duplicate_files} duplicate files (${checksumData.total_wasted_mb?.toFixed(1)} MB wasted)`,
      "info",
    );
  } else {
    showDuplicatesPlaceholder(
      container,
      `No duplicates found in ${checksumType} (${checksumData.unique_checksums} unique files)`,
    );
    document.getElementById("dup-actions").style.display = "none";
  }
}

/**
 * Handle standard (title/hash) duplicate response
 */
function handleStandardDuplicates(data, container) {
  duplicatesData = data.duplicate_groups || [];
  duplicateSelection.clear();
  document.getElementById("dup-group-count").textContent =
    duplicatesData.length;

  if (duplicatesData.length > 0) {
    renderDuplicates();
    document.getElementById("dup-actions").style.display = "flex";
  } else {
    showDuplicatesPlaceholder(container, "No duplicates found");
    document.getElementById("dup-actions").style.display = "none";
  }
}

/**
 * Find and display duplicate audiobooks
 */
async function findDuplicates() {
  const method = document.querySelector(
    'input[name="dup-method"]:checked',
  ).value;
  currentDupMode = method;

  const endpoint = DUPLICATE_ENDPOINTS[method] || DUPLICATE_ENDPOINTS["title"];
  const listContainer = document.getElementById("duplicates-list");

  showDuplicatesPlaceholder(listContainer, "Searching for duplicates...");

  try {
    const res = await fetch(`${API_BASE}${endpoint}`);
    const data = await res.json();

    const isChecksumMode =
      method === "source-checksum" || method === "library-checksum";
    if (isChecksumMode) {
      handleChecksumDuplicates(data, method, listContainer);
    } else {
      handleStandardDuplicates(data, listContainer);
    }
  } catch (error) {
    showDuplicatesPlaceholder(listContainer, "Failed to find duplicates");
    showToast("Failed to find duplicates: " + error.message, "error");
  }
}

function renderDuplicates() {
  const listContainer = document.getElementById("duplicates-list");

  listContainer.innerHTML = duplicatesData
    .map(
      (group, groupIdx) => `
        <div class="duplicate-group">
            <div class="duplicate-group-header">
                ${escapeHtml(group.title || group.hash?.substring(0, 16) + "...")}
                (${group.items.length} copies)
            </div>
            ${group.items
              .map(
                (item, itemIdx) => `
                <div class="duplicate-item ${itemIdx === 0 ? "keep" : ""}">
                    ${
                      itemIdx === 0
                        ? '<span class="keep-badge">KEEP</span>'
                        : `<input type="checkbox" class="duplicate-checkbox"
                                  data-group="${groupIdx}" data-item="${itemIdx}"
                                  data-id="${item.id}">`
                    }
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(item.title)}</div>
                        <div class="result-meta">
                            ${escapeHtml(item.author)} |
                            ${item.file_size_mb?.toFixed(1) || "?"} MB |
                            ${item.file_path ? item.file_path.split("/").pop() : "Unknown file"}
                        </div>
                    </div>
                </div>
            `,
              )
              .join("")}
        </div>
    `,
    )
    .join("");

  // Add change handlers to checkboxes
  listContainer.querySelectorAll(".duplicate-checkbox").forEach((cb) => {
    cb.addEventListener("change", updateDuplicateSelection);
  });
}

/**
 * Render checksum-based duplicates (file-based, with delete support)
 * Uses safe DOM methods to avoid XSS vulnerabilities
 */
function renderChecksumDuplicates(checksumType) {
  const listContainer = document.getElementById("duplicates-list");
  listContainer.textContent = "";

  // Store checksumType for delete operation
  listContainer.dataset.checksumType = checksumType;

  duplicatesData.forEach((group, groupIdx) => {
    const groupDiv = document.createElement("div");
    groupDiv.className = "duplicate-group";

    // Group header
    const headerDiv = document.createElement("div");
    headerDiv.className = "duplicate-group-header";
    const checksumLabel = group.checksum.substring(0, 12) + "...";
    headerDiv.textContent = `${checksumLabel} (${group.count} copies, ${group.wasted_mb?.toFixed(1) || "?"} MB wasted)`;
    groupDiv.appendChild(headerDiv);

    // Files in this group
    group.files.forEach((file, fileIdx) => {
      const itemDiv = document.createElement("div");
      itemDiv.className = "duplicate-item" + (file.is_keeper ? " keep" : "");

      // Badge for keeper, checkbox for duplicates
      if (file.is_keeper) {
        const keepBadge = document.createElement("span");
        keepBadge.className = "keep-badge";
        keepBadge.textContent = "KEEP";
        itemDiv.appendChild(keepBadge);
      } else {
        // Checkbox for deletion
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "duplicate-checkbox checksum-checkbox";
        checkbox.dataset.path = file.path;
        checkbox.dataset.group = groupIdx;
        checkbox.addEventListener("change", updateChecksumSelection);
        itemDiv.appendChild(checkbox);
      }

      // File info
      const infoDiv = document.createElement("div");
      infoDiv.className = "result-info";

      const titleDiv = document.createElement("div");
      titleDiv.className = "result-title";
      titleDiv.textContent = file.basename;
      infoDiv.appendChild(titleDiv);

      const metaDiv = document.createElement("div");
      metaDiv.className = "result-meta";

      // Extract author folder from path
      const pathParts = file.path.split("/");
      let authorFolder = "";
      if (checksumType === "sources") {
        // Sources: /hddRaid1/Audiobooks/Sources/filename.aaxc
        authorFolder = pathParts[pathParts.length - 1]; // Just filename for sources
      } else {
        // Library: /hddRaid1/Audiobooks/Library/Author/Book/file.opus
        if (pathParts.length >= 3) {
          authorFolder = pathParts[pathParts.length - 3]; // Author folder
        }
      }

      metaDiv.textContent = `${file.size_mb?.toFixed(1) || "?"} MB | ${file.asin || "No ASIN"} | ${authorFolder}`;
      infoDiv.appendChild(metaDiv);

      // Full path on hover
      itemDiv.title = file.path;

      itemDiv.appendChild(infoDiv);
      groupDiv.appendChild(itemDiv);
    });

    listContainer.appendChild(groupDiv);
  });
}

/**
 * Update selection tracking for checksum-based duplicates
 */
function updateChecksumSelection() {
  checksumPathSelection.clear();
  document.querySelectorAll(".checksum-checkbox:checked").forEach((cb) => {
    checksumPathSelection.add(cb.dataset.path);
  });

  document.getElementById("selected-count").textContent =
    checksumPathSelection.size;
  document.getElementById("delete-selected-dups").disabled =
    checksumPathSelection.size === 0;
}

function updateDuplicateSelection() {
  duplicateSelection.clear();
  document.querySelectorAll(".duplicate-checkbox:checked").forEach((cb) => {
    duplicateSelection.add(parseInt(cb.dataset.id));
  });

  document.getElementById("selected-count").textContent =
    duplicateSelection.size;
  document.getElementById("delete-selected-dups").disabled =
    duplicateSelection.size === 0;
}

function selectAllDuplicates() {
  document.querySelectorAll(".duplicate-checkbox").forEach((cb) => {
    cb.checked = true;
  });
  // Update the appropriate selection based on mode
  if (
    currentDupMode === "source-checksum" ||
    currentDupMode === "library-checksum"
  ) {
    updateChecksumSelection();
  } else {
    updateDuplicateSelection();
  }
}

function deselectAllDuplicates() {
  document.querySelectorAll(".duplicate-checkbox").forEach((cb) => {
    cb.checked = false;
  });
  // Update the appropriate selection based on mode
  if (
    currentDupMode === "source-checksum" ||
    currentDupMode === "library-checksum"
  ) {
    updateChecksumSelection();
  } else {
    updateDuplicateSelection();
  }
}

async function deleteSelectedDuplicates() {
  // Check which mode we're in
  const isChecksumMode =
    currentDupMode === "source-checksum" ||
    currentDupMode === "library-checksum";

  if (isChecksumMode) {
    // Path-based deletion for checksum duplicates
    if (checksumPathSelection.size === 0) return;

    const checksumType =
      currentDupMode === "source-checksum" ? "sources" : "library";
    const confirmed = await confirmAction(
      "Delete Duplicates",
      `Are you sure you want to delete ${checksumPathSelection.size} duplicate file(s)?\n\nThis will permanently delete the files${checksumType === "library" ? " and remove them from the database" : ""}.`,
    );

    if (!confirmed) return;

    showProgress(
      "Deleting Duplicates",
      `Removing ${checksumPathSelection.size} files...`,
    );

    try {
      const res = await fetch(`${API_BASE}/api/duplicates/delete-by-path`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paths: Array.from(checksumPathSelection),
          type: checksumType,
        }),
      });

      const result = await res.json();
      hideProgress();

      if (result.success) {
        let msg = `Deleted ${result.deleted_count} files`;
        if (result.skipped_not_found?.length > 0) {
          msg += ` (${result.skipped_not_found.length} not found)`;
        }
        if (result.errors?.length > 0) {
          msg += ` (${result.errors.length} errors)`;
        }
        showToast(msg, "success");
        findDuplicates(); // Refresh
        if (checksumType === "library") {
          loadDatabaseStats();
        }
      } else {
        showToast(result.error || "Delete failed", "error");
      }
    } catch (error) {
      hideProgress();
      showToast("Failed to delete: " + error.message, "error");
    }
  } else {
    // ID-based deletion for title/hash duplicates
    if (duplicateSelection.size === 0) return;

    const confirmed = await confirmAction(
      "Delete Duplicates",
      `Are you sure you want to delete ${duplicateSelection.size} duplicate audiobook(s)?\n\nThis will remove them from the database AND delete the audio files.`,
    );

    if (!confirmed) return;

    showProgress(
      "Deleting Duplicates",
      `Removing ${duplicateSelection.size} files...`,
    );

    try {
      const res = await fetch(`${API_BASE}/api/audiobooks/bulk-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ids: Array.from(duplicateSelection),
          delete_files: true,
        }),
      });

      const result = await res.json();
      hideProgress();

      if (result.success) {
        showToast(`Deleted ${result.deleted_count} audiobooks`, "success");
        findDuplicates(); // Refresh
        loadDatabaseStats();
      } else {
        showToast(result.error || "Delete failed", "error");
      }
    } catch (error) {
      hideProgress();
      showToast("Failed to delete: " + error.message, "error");
    }
  }
}

// ============================================
// Bulk Operations
// ============================================

function initBulkSection() {
  const filterType = document.getElementById("bulk-filter-type");
  const filterValueGroup = document.getElementById("bulk-filter-value-group");

  filterType?.addEventListener("change", () => {
    const needsValue = ["author", "narrator", "series"].includes(
      filterType.value,
    );
    filterValueGroup.style.display = needsValue ? "flex" : "none";
  });

  document
    .getElementById("bulk-load")
    ?.addEventListener("click", loadBulkAudiobooks);
  document
    .getElementById("bulk-select-all")
    ?.addEventListener("change", toggleBulkSelectAll);
  document
    .getElementById("bulk-update-btn")
    ?.addEventListener("click", bulkUpdateField);
  document
    .getElementById("bulk-delete-btn")
    ?.addEventListener("click", bulkDelete);
  initGenreManagement();
  loadGenresForPicker();
}

async function loadBulkAudiobooks() {
  const filterType = document.getElementById("bulk-filter-type").value;
  const filterValue = document.getElementById("bulk-filter-value").value;

  let endpoint = `${API_BASE}/api/audiobooks?per_page=200`;

  if (filterType === "author" && filterValue) {
    endpoint += `&author=${encodeURIComponent(filterValue)}`;
  } else if (filterType === "narrator" && filterValue) {
    endpoint += `&narrator=${encodeURIComponent(filterValue)}`;
  } else if (filterType === "series" && filterValue) {
    endpoint += `&series=${encodeURIComponent(filterValue)}`;
  } else if (filterType === "no-narrator") {
    endpoint = `${API_BASE}/api/audiobooks/missing-narrator`;
  } else if (filterType === "no-hash") {
    endpoint = `${API_BASE}/api/audiobooks/missing-hash`;
  }

  const listContainer = document.getElementById("bulk-list");
  listContainer.innerHTML = '<p class="placeholder-text">Loading...</p>';

  try {
    const res = await fetch(endpoint);
    const data = await res.json();

    const audiobooks = data.audiobooks || data || [];
    bulkSelection.clear();

    if (audiobooks.length > 0) {
      renderBulkList(audiobooks);
      document.getElementById("bulk-selection-bar").style.display = "flex";
      document.getElementById("bulk-actions-card").style.display = "block";
    } else {
      listContainer.innerHTML =
        '<p class="placeholder-text">No audiobooks found</p>';
      document.getElementById("bulk-selection-bar").style.display = "none";
      document.getElementById("bulk-actions-card").style.display = "none";
    }
  } catch (error) {
    listContainer.innerHTML =
      '<p class="placeholder-text">Failed to load audiobooks</p>';
    showToast("Failed to load: " + error.message, "error");
  }
}

function renderBulkList(audiobooks) {
  const listContainer = document.getElementById("bulk-list");

  listContainer.innerHTML = audiobooks
    .map(
      (book) => `
        <div class="bulk-item">
            <input type="checkbox" class="bulk-checkbox" data-id="${book.id}">
            <div class="result-info">
                <div class="result-title">${escapeHtml(book.title)}</div>
                <div class="result-meta">
                    ${escapeHtml(book.author)} |
                    ${escapeHtml(book.narrator || "No narrator")} |
                    ${book.series ? escapeHtml(book.series) : "No series"}
                </div>
            </div>
        </div>
    `,
    )
    .join("");

  // Add change handlers
  listContainer.querySelectorAll(".bulk-checkbox").forEach((cb) => {
    cb.addEventListener("change", updateBulkSelection);
  });
}

function updateBulkSelection() {
  bulkSelection.clear();
  document.querySelectorAll(".bulk-checkbox:checked").forEach((cb) => {
    bulkSelection.add(parseInt(cb.dataset.id));
  });

  document.getElementById("bulk-selected-count").textContent =
    bulkSelection.size;
  document.getElementById("bulk-select-all").checked =
    bulkSelection.size === document.querySelectorAll(".bulk-checkbox").length;
}

function toggleBulkSelectAll() {
  const selectAll = document.getElementById("bulk-select-all").checked;
  document.querySelectorAll(".bulk-checkbox").forEach((cb) => {
    cb.checked = selectAll;
  });
  updateBulkSelection();
}

async function bulkUpdateField() {
  if (bulkSelection.size === 0) {
    showToast("No audiobooks selected", "error");
    return;
  }

  const field = document.getElementById("bulk-update-field").value;
  const value = document.getElementById("bulk-update-value").value;

  if (!field) {
    showToast("Please select a field to update", "error");
    return;
  }

  try {
    const result = await safeFetch(`${API_BASE}/api/audiobooks/bulk-update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids: Array.from(bulkSelection),
        field: field,
        value: value,
      }),
    });

    if (result.success) {
      showToast(`Updated ${result.updated_count} audiobooks`, "success");
      loadBulkAudiobooks(); // Refresh
    } else {
      showToast(result.error || "Update failed", "error");
    }
  } catch (error) {
    showToast("Failed to update: " + error.message, "error");
  }
}

async function bulkDelete() {
  if (bulkSelection.size === 0) {
    showToast("No audiobooks selected", "error");
    return;
  }

  const confirmed = await confirmAction(
    "Delete Audiobooks",
    `Are you sure you want to delete ${bulkSelection.size} audiobook(s)?\n\nThis will remove them from the database. The audio files will NOT be deleted.`,
  );

  if (!confirmed) return;

  try {
    const result = await safeFetch(`${API_BASE}/api/audiobooks/bulk-delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids: Array.from(bulkSelection),
        delete_files: false,
      }),
    });

    if (result.success) {
      showToast(
        `Deleted ${result.deleted_count} audiobooks from database`,
        "success",
      );
      loadBulkAudiobooks(); // Refresh
      loadDatabaseStats();
    } else {
      showToast(result.error || "Delete failed", "error");
    }
  } catch (error) {
    showToast("Failed to delete: " + error.message, "error");
  }
}

// ============================================
// Genre Management (Bulk Ops)
// ============================================

const genreSelection = new Set();

function initGenreManagement() {
  document
    .getElementById("bulk-genre-apply")
    ?.addEventListener("click", applyBulkGenres);
  document
    .getElementById("genre-new-btn")
    ?.addEventListener("click", addNewGenreToList);
  const newInput = document.getElementById("genre-new-input");
  newInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addNewGenreToList();
    }
  });
}

async function loadGenresForPicker() {
  const picker = document.getElementById("genre-picker");
  if (!picker) return;

  try {
    const genres = await safeFetch(`${API_BASE}/api/genres`);
    genreSelection.clear();

    while (picker.firstChild) picker.removeChild(picker.firstChild);

    if (!genres || genres.length === 0) {
      const p = document.createElement("p");
      p.className = "placeholder-text";
      p.textContent = "No genres found in database";
      picker.appendChild(p);
      return;
    }

    genres.forEach((genre) => {
      const label = document.createElement("label");
      label.className = "genre-tag";
      label.title = `${genre.book_count} book(s)`;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "genre-checkbox";
      checkbox.value = genre.name;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          genreSelection.add(genre.name);
          label.classList.add("selected");
        } else {
          genreSelection.delete(genre.name);
          label.classList.remove("selected");
        }
      });

      const span = document.createElement("span");
      span.textContent = genre.name;

      const count = document.createElement("small");
      count.className = "genre-count";
      count.textContent = `(${genre.book_count})`;

      label.appendChild(checkbox);
      label.appendChild(span);
      label.appendChild(count);
      picker.appendChild(label);
    });
  } catch (error) {
    const p = document.createElement("p");
    p.className = "placeholder-text";
    p.textContent = "Failed to load genres";
    while (picker.firstChild) picker.removeChild(picker.firstChild);
    picker.appendChild(p);
  }
}

function addNewGenreToList() {
  const input = document.getElementById("genre-new-input");
  const name = input.value.trim();
  if (!name) return;

  const picker = document.getElementById("genre-picker");
  // Check if genre already exists in picker
  const existing = picker.querySelector(`input[value="${CSS.escape(name)}"]`);
  if (existing) {
    existing.checked = true;
    existing.dispatchEvent(new Event("change"));
    input.value = "";
    return;
  }

  // Add new genre tag to picker
  const label = document.createElement("label");
  label.className = "genre-tag selected new-genre";
  label.title = "New genre (will be created)";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "genre-checkbox";
  checkbox.value = name;
  checkbox.checked = true;
  genreSelection.add(name);

  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      genreSelection.add(name);
      label.classList.add("selected");
    } else {
      genreSelection.delete(name);
      label.classList.remove("selected");
    }
  });

  const span = document.createElement("span");
  span.textContent = name;

  const count = document.createElement("small");
  count.className = "genre-count";
  count.textContent = "(new)";

  label.appendChild(checkbox);
  label.appendChild(span);
  label.appendChild(count);
  picker.appendChild(label);

  input.value = "";
}

async function applyBulkGenres() {
  if (bulkSelection.size === 0) {
    showToast("No audiobooks selected", "error");
    return;
  }

  if (genreSelection.size === 0) {
    showToast("No genres selected", "error");
    return;
  }

  const mode =
    document.querySelector('input[name="genre-mode"]:checked')?.value || "add";
  const action = mode === "add" ? "add" : "remove";
  const genreList = Array.from(genreSelection).join(", ");

  const confirmed = await confirmAction(
    `${mode === "add" ? "Add" : "Remove"} Genres`,
    `${mode === "add" ? "Add" : "Remove"} ${genreSelection.size} genre(s) ${mode === "add" ? "to" : "from"} ${bulkSelection.size} audiobook(s)?\n\nGenres: ${genreList}`,
  );

  if (!confirmed) return;

  try {
    const result = await safeFetch(`${API_BASE}/api/audiobooks/bulk-genres`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids: Array.from(bulkSelection),
        genres: Array.from(genreSelection),
        mode: action,
      }),
    });

    if (result.success) {
      const verb = mode === "add" ? "Added" : "Removed";
      showToast(
        `${verb} ${result.genre_count} genre(s) for ${result.book_count} book(s)`,
        "success",
      );
      // Clear genre selection
      genreSelection.clear();
      document
        .querySelectorAll(".genre-tag")
        .forEach((tag) => tag.classList.remove("selected"));
      document.querySelectorAll(".genre-checkbox").forEach((cb) => {
        cb.checked = false;
      });
      // Reload genre list to update counts
      loadGenresForPicker();
    } else {
      showToast(result.error || "Genre update failed", "error");
    }
  } catch (error) {
    showToast("Failed to update genres: " + error.message, "error");
  }
}

// ============================================
// Modals & Toasts
// ============================================

function initModals() {
  document
    .getElementById("modal-close")
    ?.addEventListener("click", hideConfirmModal);
  document
    .getElementById("confirm-cancel")
    ?.addEventListener("click", hideConfirmModal);
}

let confirmResolve = null;

function confirmAction(title, message) {
  return new Promise((resolve) => {
    confirmResolve = resolve;

    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-body").textContent = message;
    document.getElementById("confirm-modal").classList.add("active");

    const confirmBtn = document.getElementById("confirm-action");
    confirmBtn.onclick = () => {
      confirmResolve = null; // Clear before hide so it won't resolve false
      hideConfirmModal();
      resolve(true);
    };
  });
}

function hideConfirmModal() {
  document.getElementById("confirm-modal").classList.remove("active");
  if (confirmResolve) {
    confirmResolve(false);
    confirmResolve = null;
  }
}

/**
 * Show confirmation modal with custom callback.
 * Used for actions that need custom handling like delete user.
 */
function showConfirmModal(title, message, onConfirm, onCancel) {
  const modal = document.getElementById("confirm-modal");
  const titleEl = document.getElementById("confirm-title");
  const bodyEl = document.getElementById("confirm-body");
  const confirmBtn = document.getElementById("confirm-action");
  const cancelBtn = document.getElementById("confirm-cancel");

  titleEl.textContent = title;
  bodyEl.textContent = message;
  confirmBtn.textContent = "Confirm";
  confirmBtn.className = "office-btn danger";

  modal.classList.add("active");

  // Clone to remove old event listeners
  const newConfirmBtn = confirmBtn.cloneNode(true);
  confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

  newConfirmBtn.addEventListener("click", () => {
    modal.classList.remove("active");
    if (onConfirm) onConfirm();
  });

  // Handle cancel button if onCancel callback provided
  if (onCancel && cancelBtn) {
    const newCancelBtn = cancelBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
    newCancelBtn.addEventListener("click", () => {
      modal.classList.remove("active");
      onCancel();
    });
  }
}

function showProgress(title, message) {
  document.getElementById("progress-title").textContent = title;
  document.getElementById("progress-message").textContent = message;
  document.getElementById("progress-output").textContent = "";
  document.getElementById("progress-modal").classList.add("active");
}

function hideProgress() {
  document.getElementById("progress-modal").classList.remove("active");
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ============================================
// Utilities
// ============================================

function escapeHtml(text) {
  if (!text) return "";
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ============================================
// Operation Status Tracking & Polling
// ============================================

function initOperationStatus() {
  // Cancel button in status banner
  document
    .getElementById("status-cancel-btn")
    ?.addEventListener("click", cancelActiveOperation);

  // Close button in progress modal
  document
    .getElementById("modal-close-progress")
    ?.addEventListener("click", hideProgressModal);
}

async function checkActiveOperations() {
  try {
    const res = await fetch(`${API_BASE}/api/operations/active`);
    const data = await res.json();

    if (data.operations && data.operations.length > 0) {
      // Resume tracking the first active operation
      const op = data.operations[0];
      activeOperationId = op.id;
      showStatusBanner(op);
      startOperationPolling();
    }
  } catch (error) {
    console.error("Failed to check active operations:", error);
  }
}

function startOperationPolling() {
  if (pollingInterval) {
    clearInterval(pollingInterval);
  }

  pollingInterval = setInterval(pollOperationStatus, 500);
}

function stopOperationPolling() {
  if (pollingInterval) {
    clearInterval(pollingInterval);
    pollingInterval = null;
  }
}

async function pollOperationStatus() {
  if (!activeOperationId) {
    stopOperationPolling();
    return;
  }

  try {
    const res = await fetch(
      `${API_BASE}/api/operations/status/${activeOperationId}`,
    );
    const status = await res.json();

    updateStatusDisplay(status);

    // Check if operation completed
    if (status.state !== "running" && status.state !== "pending") {
      stopOperationPolling();
      handleOperationComplete(status);
    }
  } catch (error) {
    console.error("Polling error:", error);
  }
}

function updateStatusDisplay(status) {
  // Update banner
  document.getElementById("status-operation-name").textContent =
    status.description || `${status.type} operation`;
  document.getElementById("status-progress-fill").style.width =
    `${status.progress}%`;
  document.getElementById("status-progress-percent").textContent =
    `${status.progress}%`;
  document.getElementById("status-message").textContent =
    status.message || "Processing...";

  // Update modal if visible
  const modal = document.getElementById("progress-modal");
  if (modal.classList.contains("active")) {
    document.getElementById("modal-progress-fill").style.width =
      `${status.progress}%`;
    document.getElementById("modal-progress-percent").textContent =
      `${status.progress}%`;
    document.getElementById("progress-message").textContent =
      status.message || "Processing...";

    if (status.elapsed_seconds) {
      const elapsed = Math.round(status.elapsed_seconds);
      document.getElementById("modal-progress-elapsed").textContent =
        `Elapsed: ${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
    }

    document.getElementById("modal-operation-id").textContent =
      `ID: ${status.id}`;
  }
}

function handleOperationComplete(status) {
  activeOperationId = null;
  hideStatusBanner();

  // Show modal close button
  document
    .getElementById("modal-close-progress")
    ?.style.setProperty("display", "inline-block");

  // Update modal with final status
  const modal = document.getElementById("progress-modal");
  if (modal.classList.contains("active")) {
    document.getElementById("progress-spinner")?.classList.add("hidden");

    if (status.state === "completed") {
      document.getElementById("progress-message").textContent =
        "Operation completed successfully!";
      document.getElementById("modal-progress-fill").style.backgroundColor =
        "#2e7d32";

      // Show result details
      if (status.result) {
        const output = document.getElementById("progress-output");
        let resultText = "";
        if (status.result.added !== undefined) {
          resultText = `Added: ${status.result.added} audiobooks`;
          if (status.result.skipped)
            resultText += ` | Skipped: ${status.result.skipped}`;
          if (status.result.errors)
            resultText += ` | Errors: ${status.result.errors}`;
        } else if (status.result.files_found !== undefined) {
          resultText = `Files found: ${status.result.files_found}`;
        } else if (status.result.imported_count !== undefined) {
          resultText = `Imported: ${status.result.imported_count} audiobooks`;
        } else if (status.result.hashes_generated !== undefined) {
          resultText = `Hashes generated: ${status.result.hashes_generated}`;
        } else if (status.result.source_checksums !== undefined) {
          resultText = `Sources: ${status.result.source_checksums} checksums | Library: ${status.result.library_checksums} checksums`;
        }
        output.textContent = resultText;
      }

      showToast("Operation completed successfully", "success");
    } else if (status.state === "failed") {
      document.getElementById("progress-message").textContent =
        "Operation failed";
      document.getElementById("modal-progress-fill").style.backgroundColor =
        "#c62828";
      document.getElementById("progress-output").textContent =
        status.error || "Unknown error";
      showToast(`Operation failed: ${status.error}`, "error");
    } else if (status.state === "cancelled") {
      document.getElementById("progress-message").textContent =
        "Operation cancelled";
      showToast("Operation cancelled", "info");
    }
  } else {
    // Modal not visible, just show toast
    if (status.state === "completed") {
      showToast("Background operation completed", "success");
    } else if (status.state === "failed") {
      showToast(`Background operation failed: ${status.error}`, "error");
    }
  }

  // Refresh stats
  loadDatabaseStats();
}

function showStatusBanner(status) {
  const banner = document.getElementById("operation-status-banner");
  banner.style.display = "block";
  updateStatusDisplay(status);
}

function hideStatusBanner() {
  document.getElementById("operation-status-banner").style.display = "none";
}

function hideProgressModal() {
  document.getElementById("progress-modal").classList.remove("active");
  // Reset modal state
  document.getElementById("modal-progress-fill").style.width = "0%";
  document.getElementById("modal-progress-fill").style.backgroundColor = "";
  document.getElementById("modal-close-progress").style.display = "none";
  document.getElementById("progress-output").textContent = "";
  document.getElementById("modal-progress-elapsed").textContent = "";
}

async function cancelActiveOperation() {
  if (!activeOperationId) return;

  try {
    await fetch(`${API_BASE}/api/operations/cancel/${activeOperationId}`, {
      method: "POST",
    });
    showToast("Cancellation requested", "info");
  } catch (error) {
    showToast("Failed to cancel operation", "error");
  }
}

// ============================================
// Async Operations with Progress Tracking
// ============================================

async function addNewAudiobooks() {
  // Check if already running
  if (activeOperationId) {
    showToast("An operation is already running", "error");
    return;
  }

  showProgressModal("Adding New Audiobooks", "Scanning for new files...");

  try {
    const res = await fetch(`${API_BASE}/api/utilities/add-new`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ calculate_hashes: true }),
    });

    const result = await res.json();

    if (result.success) {
      activeOperationId = result.operation_id;
      showStatusBanner({
        id: result.operation_id,
        description: "Adding new audiobooks",
        progress: 0,
      });
      startOperationPolling();
    } else {
      hideProgressModal();
      if (result.operation_id) {
        // Already running
        activeOperationId = result.operation_id;
        showStatusBanner({
          id: result.operation_id,
          description: "Adding new audiobooks",
          progress: 0,
        });
        startOperationPolling();
        showToast("Operation already in progress", "info");
      } else {
        showToast(result.error || "Failed to start operation", "error");
      }
    }
  } catch (error) {
    hideProgressModal();
    showToast("Failed to start add operation: " + error.message, "error");
  }
}

async function rescanLibraryAsync() {
  if (activeOperationId) {
    showToast("An operation is already running", "error");
    return;
  }

  if (
    !(await confirmAction(
      "Full Library Rescan",
      'This will scan ALL files in the library, which can take a long time for large libraries.\n\nFor adding new books only, use "Add New" instead.\n\nContinue with full rescan?',
    ))
  ) {
    return;
  }

  showProgressModal("Scanning Library", "Starting full library scan...");

  try {
    const res = await fetch(`${API_BASE}/api/utilities/rescan-async`, {
      method: "POST",
    });
    const result = await res.json();

    if (result.success) {
      activeOperationId = result.operation_id;
      showStatusBanner({
        id: result.operation_id,
        description: "Full library scan",
        progress: 0,
      });
      startOperationPolling();
    } else {
      hideProgressModal();
      showToast(result.error || "Failed to start scan", "error");
    }
  } catch (error) {
    hideProgressModal();
    showToast("Failed to start scan: " + error.message, "error");
  }
}

async function reimportDatabaseAsync() {
  if (activeOperationId) {
    showToast("An operation is already running", "error");
    return;
  }

  if (
    !(await confirmAction(
      "Reimport Database",
      "This will rebuild the database from scan results. Existing narrator and genre data will be preserved. Continue?",
    ))
  ) {
    return;
  }

  showProgressModal("Reimporting Database", "Starting database import...");

  try {
    const res = await fetch(`${API_BASE}/api/utilities/reimport-async`, {
      method: "POST",
    });
    const result = await res.json();

    if (result.success) {
      activeOperationId = result.operation_id;
      showStatusBanner({
        id: result.operation_id,
        description: "Database import",
        progress: 0,
      });
      startOperationPolling();
    } else {
      hideProgressModal();
      showToast(result.error || "Failed to start import", "error");
    }
  } catch (error) {
    hideProgressModal();
    showToast("Failed to start import: " + error.message, "error");
  }
}

async function generateHashesAsync() {
  if (activeOperationId) {
    showToast("An operation is already running", "error");
    return;
  }

  showProgressModal("Generating Hashes", "Calculating SHA-256 hashes...");

  try {
    const res = await fetch(`${API_BASE}/api/utilities/generate-hashes-async`, {
      method: "POST",
    });
    const result = await res.json();

    if (result.success) {
      activeOperationId = result.operation_id;
      showStatusBanner({
        id: result.operation_id,
        description: "Hash generation",
        progress: 0,
      });
      startOperationPolling();
    } else {
      hideProgressModal();
      showToast(result.error || "Failed to start hash generation", "error");
    }
  } catch (error) {
    hideProgressModal();
    showToast("Failed to start hash generation: " + error.message, "error");
  }
}

async function generateChecksumsAsync() {
  if (activeOperationId) {
    showToast("An operation is already running", "error");
    return;
  }

  showProgressModal(
    "Generating Checksums",
    "Calculating MD5 checksums for Sources and Library...",
  );

  try {
    const res = await fetch(
      `${API_BASE}/api/utilities/generate-checksums-async`,
      { method: "POST" },
    );
    const result = await res.json();

    if (result.success) {
      activeOperationId = result.operation_id;
      showStatusBanner({
        id: result.operation_id,
        description: "Checksum generation",
        progress: 0,
      });
      startOperationPolling();
    } else {
      hideProgressModal();
      showToast(result.error || "Failed to start checksum generation", "error");
    }
  } catch (error) {
    hideProgressModal();
    showToast("Failed to start checksum generation: " + error.message, "error");
  }
}

function showProgressModal(title, message) {
  document.getElementById("progress-title").textContent = title;
  document.getElementById("progress-message").textContent = message;
  document.getElementById("progress-output").textContent = "";
  document.getElementById("modal-progress-fill").style.width = "0%";
  document.getElementById("modal-progress-fill").style.backgroundColor = "";
  document.getElementById("modal-progress-percent").textContent = "0%";
  document.getElementById("modal-progress-elapsed").textContent = "";
  document.getElementById("modal-close-progress").style.display = "none";
  document.getElementById("progress-modal").classList.add("active");
}

// ============================================
// Conversion Monitor Section
// ============================================

let conversionRefreshInterval = null;
let conversionRateTracker = {
  prevCount: null, // null = not yet initialized
  prevTime: Date.now(),
  rate: 0,
  stableTime: 0, // track how long count has been unchanged
  prevReadBytes: 0,
  prevWriteBytes: 0,
  readThroughput: 0, // bytes per second
  writeThroughput: 0,
};

// Per-job throughput tracking
let jobThroughputTracker = {}; // pid -> { prevReadBytes, throughput }
let conversionSortBy = "percent"; // 'percent', 'throughput', 'name'

function initConversionSection() {
  // Refresh button
  document
    .getElementById("conv-refresh")
    ?.addEventListener("click", loadConversionStatus);

  // Auto-refresh toggle
  document
    .getElementById("conv-auto-refresh")
    ?.addEventListener("change", (e) => {
      if (e.target.checked) {
        startConversionAutoRefresh();
      } else {
        stopConversionAutoRefresh();
      }
    });

  // Refresh interval change
  document
    .getElementById("conv-refresh-interval")
    ?.addEventListener("change", () => {
      if (document.getElementById("conv-auto-refresh")?.checked) {
        stopConversionAutoRefresh();
        startConversionAutoRefresh();
      }
    });

  // Start auto-refresh if checkbox is checked
  if (document.getElementById("conv-auto-refresh")?.checked) {
    startConversionAutoRefresh();
  }

  // Expandable details panel toggle
  const rateToggle = document.getElementById("conv-rate-toggle");
  const detailsPanel = document.getElementById("conv-details-panel");
  if (rateToggle && detailsPanel) {
    rateToggle.addEventListener("click", () => {
      rateToggle.classList.toggle("expanded");
      detailsPanel.classList.toggle("expanded");
    });
  }

  // Sort button handlers
  document.querySelectorAll(".sort-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sortBy = btn.dataset.sort;
      if (sortBy) {
        conversionSortBy = sortBy;
        // Update active state
        document
          .querySelectorAll(".sort-btn")
          .forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        // Refresh to apply sort
        loadConversionStatus();
      }
    });
  });
}

function startConversionAutoRefresh() {
  stopConversionAutoRefresh();
  const intervalSec = parseInt(
    document.getElementById("conv-refresh-interval")?.value || "10",
  );
  loadConversionStatus();
  conversionRefreshInterval = setInterval(
    loadConversionStatus,
    intervalSec * 1000,
  );
}

function stopConversionAutoRefresh() {
  if (conversionRefreshInterval) {
    clearInterval(conversionRefreshInterval);
    conversionRefreshInterval = null;
  }
}

// ============================================
// Conversion Status Helper Functions
// ============================================

/**
 * Format bytes to human-readable string (KiB, MiB, GiB, etc.)
 */
function formatBytes(bytes, decimals = 1) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KiB", "MiB", "GiB", "TiB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return (
    parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + " " + sizes[i]
  );
}

/**
 * Update the conversion progress bar UI
 */
function updateConversionProgressBar(status) {
  const progressFill = document.getElementById("conv-progress-fill");
  const percentDisplay = document.getElementById("conv-percent");
  if (!progressFill || !percentDisplay) return;

  progressFill.style.width = `${status.percent_complete}%`;
  percentDisplay.textContent = `${status.percent_complete}%`;

  const container = progressFill.closest(".conversion-progress-container");
  if (container) {
    container.classList.toggle("conversion-complete", status.is_complete);
  }
}

/**
 * Calculate conversion rate and update tracker state
 * Returns elapsed time in seconds for use by other calculations
 */
function calculateConversionRate(status) {
  const now = Date.now();
  const elapsed = (now - conversionRateTracker.prevTime) / 1000;

  if (conversionRateTracker.prevCount === null) {
    // First observation - initialize baseline
    conversionRateTracker.prevCount = status.total_converted;
    conversionRateTracker.prevTime = now;
    conversionRateTracker.stableTime = 0;
  } else if (status.total_converted > conversionRateTracker.prevCount) {
    // Conversions happened - calculate rate
    const delta = status.total_converted - conversionRateTracker.prevCount;
    conversionRateTracker.rate = (delta * 60) / elapsed;
    conversionRateTracker.prevCount = status.total_converted;
    conversionRateTracker.prevTime = now;
    conversionRateTracker.stableTime = 0;
  } else {
    // No new conversions - track stable time
    conversionRateTracker.stableTime += elapsed;
    conversionRateTracker.prevTime = now;
    if (conversionRateTracker.stableTime > 30) {
      conversionRateTracker.rate = 0;
    }
  }

  return elapsed;
}

/**
 * Get display text for conversion rate
 */
function getConversionRateText(status, processes) {
  if (status.is_complete) return "complete";
  if (conversionRateTracker.rate > 0)
    return `${conversionRateTracker.rate.toFixed(1)} books/min`;
  if (processes.ffmpeg_count > 0) return `${processes.ffmpeg_count} active`;
  if (conversionRateTracker.stableTime > 10) return "idle";
  return "measuring...";
}

/**
 * Get display text for ETA
 */
function getETAText(status) {
  if (status.is_complete) return "Complete!";
  if (conversionRateTracker.rate > 0 && status.remaining > 0) {
    const etaMins = status.remaining / conversionRateTracker.rate;
    if (etaMins < 1) return `ETA: ${Math.round(etaMins * 60)}s`;
    if (etaMins < 60) return `ETA: ${Math.round(etaMins)}m`;
    const hours = Math.floor(etaMins / 60);
    const mins = Math.round(etaMins % 60);
    return `ETA: ${hours}h ${mins}m`;
  }
  return "Calculating...";
}

/**
 * Update all file count displays
 */
function updateConversionCounts(status) {
  document.getElementById("conv-source-count").textContent =
    status.source_count.toLocaleString();
  document.getElementById("conv-library-count").textContent =
    status.library_count.toLocaleString();
  document.getElementById("conv-staged-count").textContent =
    status.staged_count.toLocaleString();
  document.getElementById("conv-remaining-count").textContent =
    status.remaining.toLocaleString();
  document.getElementById("conv-queue-count").textContent =
    status.queue_count.toLocaleString();

  // Update remaining summary box
  const remainingTotal = document.getElementById("remaining-total");
  const sourceTotal = document.getElementById("source-total");
  const summaryBox = document.getElementById("remaining-summary");
  if (remainingTotal && sourceTotal) {
    remainingTotal.textContent = status.remaining.toLocaleString();
    sourceTotal.textContent = status.source_count.toLocaleString();
    if (summaryBox) {
      summaryBox.classList.toggle("complete", status.remaining === 0);
    }
  }
}

/**
 * Update system stats display (ffmpeg count, load avg, tmpfs)
 */
function updateConversionSystemStats(processes, system) {
  document.getElementById("conv-ffmpeg-count").textContent =
    processes.ffmpeg_count || "0";
  document.getElementById("conv-ffmpeg-nice").textContent =
    processes.ffmpeg_nice || "-";
  document.getElementById("conv-load-avg").textContent = system.load_avg || "-";
  document.getElementById("conv-tmpfs-usage").textContent =
    system.tmpfs_usage || "-";
  document.getElementById("conv-tmpfs-avail").textContent =
    system.tmpfs_avail || "-";

  const activeBadge = document.getElementById("conv-active-count");
  if (activeBadge) {
    activeBadge.textContent = `${processes.ffmpeg_count} active`;
  }
}

/**
 * Calculate per-job throughput and update tracker
 */
function calculateJobThroughput(jobs, elapsed) {
  const newTracker = {};
  jobs.forEach((job) => {
    const pid = job.pid;
    const currentReadBytes = job.read_bytes || 0;

    if (jobThroughputTracker[pid] && elapsed > 0) {
      const delta = currentReadBytes - jobThroughputTracker[pid].prevReadBytes;
      job.throughput = delta >= 0 ? delta / elapsed : 0;
    } else {
      job.throughput = 0;
    }
    newTracker[pid] = { prevReadBytes: currentReadBytes };
  });
  jobThroughputTracker = newTracker;
}

/**
 * Sort jobs based on current sort criteria
 */
function sortConversionJobs(jobs) {
  return [...jobs].sort((a, b) => {
    switch (conversionSortBy) {
      case "percent":
        return (b.percent || 0) - (a.percent || 0);
      case "throughput":
        return (b.throughput || 0) - (a.throughput || 0);
      case "name":
        return (a.filename || "").localeCompare(b.filename || "");
      default:
        return 0;
    }
  });
}

/**
 * Create DOM element for a single conversion job
 */
function createJobElement(job) {
  const itemDiv = document.createElement("div");
  itemDiv.className = "active-conversion-item";

  const filenameSpan = document.createElement("span");
  filenameSpan.className = "filename";
  filenameSpan.textContent = job.display_name || job.filename || "unknown";
  itemDiv.appendChild(filenameSpan);

  const statsDiv = document.createElement("div");
  statsDiv.className = "job-stats";

  const percentSpan = document.createElement("span");
  percentSpan.className = "job-percent";
  percentSpan.textContent = `${job.percent || 0}%`;
  statsDiv.appendChild(percentSpan);

  const throughputSpan = document.createElement("span");
  throughputSpan.className = "job-throughput";
  const throughputMiB = (job.throughput || 0) / 1048576;
  throughputSpan.textContent =
    throughputMiB > 0.1 ? `${throughputMiB.toFixed(1)} MiB/s` : "—";
  statsDiv.appendChild(throughputSpan);

  const readSpan = document.createElement("span");
  readSpan.className = "job-read";
  const readMiB = (job.read_bytes || 0) / 1048576;
  const sourceMiB = (job.source_size || 0) / 1048576;
  readSpan.textContent = `${readMiB.toFixed(0)}/${sourceMiB.toFixed(0)} MiB`;
  statsDiv.appendChild(readSpan);

  itemDiv.appendChild(statsDiv);
  return itemDiv;
}

/**
 * Render active conversions list
 */
function renderActiveConversionsList(processes, elapsed) {
  const activeList = document.getElementById("conv-active-list");
  if (!activeList) return;

  // Clear existing content
  while (activeList.firstChild) {
    activeList.removeChild(activeList.firstChild);
  }

  let jobs = processes.conversion_jobs || [];
  if (jobs.length > 0) {
    calculateJobThroughput(jobs, elapsed);
    jobs = sortConversionJobs(jobs);
    jobs.forEach((job) => activeList.appendChild(createJobElement(job)));
  } else if (processes.active_conversions?.length > 0) {
    // Fallback to legacy format
    processes.active_conversions.forEach((filename) => {
      const itemDiv = document.createElement("div");
      itemDiv.className = "active-conversion-item";
      const filenameSpan = document.createElement("span");
      filenameSpan.className = "filename";
      filenameSpan.textContent = filename;
      itemDiv.appendChild(filenameSpan);
      activeList.appendChild(itemDiv);
    });
  } else {
    const placeholder = document.createElement("p");
    placeholder.className = "placeholder-text";
    placeholder.textContent = "No active conversions";
    activeList.appendChild(placeholder);
  }
}

/**
 * Update I/O throughput tracking and display
 */
function updateIOThroughput(processes, elapsed) {
  const currentReadBytes = processes.io_read_bytes || 0;
  const currentWriteBytes = processes.io_write_bytes || 0;

  if (conversionRateTracker.prevReadBytes > 0 && elapsed > 0) {
    const readDelta = currentReadBytes - conversionRateTracker.prevReadBytes;
    const writeDelta = currentWriteBytes - conversionRateTracker.prevWriteBytes;
    if (readDelta >= 0)
      conversionRateTracker.readThroughput = readDelta / elapsed;
    if (writeDelta >= 0)
      conversionRateTracker.writeThroughput = writeDelta / elapsed;
  }
  conversionRateTracker.prevReadBytes = currentReadBytes;
  conversionRateTracker.prevWriteBytes = currentWriteBytes;

  // Update display
  const readThroughputEl = document.getElementById("conv-read-throughput");
  const writeThroughputEl = document.getElementById("conv-write-throughput");
  const isActive = processes.ffmpeg_count > 0;

  if (readThroughputEl) {
    readThroughputEl.textContent = isActive
      ? `${formatBytes(conversionRateTracker.readThroughput)}/s`
      : "idle";
  }
  if (writeThroughputEl) {
    writeThroughputEl.textContent = isActive
      ? `${formatBytes(conversionRateTracker.writeThroughput)}/s`
      : "idle";
  }

  const totalReadEl = document.getElementById("conv-total-read");
  const totalWriteEl = document.getElementById("conv-total-write");
  if (totalReadEl) totalReadEl.textContent = formatBytes(currentReadBytes);
  if (totalWriteEl) totalWriteEl.textContent = formatBytes(currentWriteBytes);
}

/**
 * Update queue breakdown details
 */
function updateQueueBreakdown(status, processes) {
  const waitingInQueue = Math.max(
    0,
    status.queue_count - processes.ffmpeg_count,
  );

  const activeDetailEl = document.getElementById("conv-active-detail");
  const queuedDetailEl = document.getElementById("conv-queued-detail");
  const stagingDetailEl = document.getElementById("conv-staging-detail");
  const unqueuedDetailEl = document.getElementById("conv-unqueued-detail");

  if (activeDetailEl) activeDetailEl.textContent = processes.ffmpeg_count;
  if (queuedDetailEl) queuedDetailEl.textContent = waitingInQueue;
  if (stagingDetailEl) stagingDetailEl.textContent = status.staged_count;
  if (unqueuedDetailEl) unqueuedDetailEl.textContent = 0;
}

/**
 * Update active files panel in details section
 */
function updateActiveFilesPanel(processes) {
  const activeFilesEl = document.getElementById("conv-active-files");
  if (!activeFilesEl) return;

  while (activeFilesEl.firstChild) {
    activeFilesEl.removeChild(activeFilesEl.firstChild);
  }

  if (processes.active_conversions?.length > 0) {
    processes.active_conversions.forEach((filename) => {
      const itemDiv = document.createElement("div");
      itemDiv.className = "active-file-item";
      const filenameSpan = document.createElement("span");
      filenameSpan.className = "filename";
      filenameSpan.textContent = filename;
      itemDiv.appendChild(filenameSpan);
      activeFilesEl.appendChild(itemDiv);
    });
  } else {
    const noFiles = document.createElement("span");
    noFiles.className = "no-files";
    noFiles.textContent = "No active conversions";
    activeFilesEl.appendChild(noFiles);
  }
}

// ============================================
// Main Conversion Status Function (Refactored)
// ============================================

/**
 * Load and display conversion status
 * Orchestrates calls to helper functions for each UI section
 */
async function loadConversionStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/conversion/status`);
    const data = await res.json();

    if (!data.success) {
      console.error("Failed to load conversion status:", data.error);
      return;
    }

    const { status, processes, system } = data;

    // Update progress bar
    updateConversionProgressBar(status);

    // Calculate rate (returns elapsed time for other calculations)
    const elapsed = calculateConversionRate(status);

    // Update rate display
    const rateDisplay = document.getElementById("conv-rate");
    if (rateDisplay) {
      rateDisplay.textContent = getConversionRateText(status, processes);
    }

    // Update ETA display
    const etaDisplay = document.getElementById("conv-eta");
    if (etaDisplay) {
      etaDisplay.textContent = getETAText(status);
    }

    // Update file counts
    updateConversionCounts(status);

    // Update system stats
    updateConversionSystemStats(processes, system);

    // Render active conversions list
    renderActiveConversionsList(processes, elapsed);

    // Update I/O throughput
    updateIOThroughput(processes, elapsed);

    // Update queue breakdown
    updateQueueBreakdown(status, processes);

    // Update active files panel
    updateActiveFilesPanel(processes);

    // Update timestamp
    const lastUpdated = document.getElementById("conv-last-updated");
    if (lastUpdated) {
      lastUpdated.textContent = `Updated: ${new Date().toLocaleTimeString()}`;
    }
  } catch (error) {
    console.error("Failed to load conversion status:", error);
  }
}

// ============================================

// ============================================
// Activity Audit Section
// ============================================

// Activity audit state
let activityOffset = 0;
const ACTIVITY_PAGE_SIZE = 25;

function refreshLiveConnections() {
  fetch("/api/admin/connections")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var container = document.getElementById("activity-connections");
      if (!container) {
        container = document.createElement("div");
        container.id = "activity-connections";
        container.className = "connections-card";
        var actSection = document.getElementById("activity-section");
        if (actSection) {
          var sectionHeader = actSection.querySelector(".section-header");
          if (sectionHeader && sectionHeader.nextSibling) {
            actSection.insertBefore(container, sectionHeader.nextSibling);
          } else {
            actSection.insertBefore(container, actSection.firstChild);
          }
        }
      }
      while (container.firstChild) container.removeChild(container.firstChild);

      var heading = document.createElement("h3");
      heading.textContent = "Live Connections: " + data.count;
      container.appendChild(heading);

      if (data.users && data.users.length > 0) {
        var ul = document.createElement("ul");
        ul.className = "connections-list";
        data.users.forEach(function (u) {
          var li = document.createElement("li");
          li.textContent = u.username + " (" + u.state + ")";
          ul.appendChild(li);
        });
        container.appendChild(ul);
      }
    })
    .catch(function () {});
}

function initActivitySection() {
  // Initial load after a short delay to let WebSocket connect first
  setTimeout(refreshLiveConnections, 1500);

  // Refresh connections when WebSocket opens (audioWs is from websocket.js)
  document.addEventListener("ws-connected", refreshLiveConnections);

  // Periodically refresh connections count (every 30s)
  setInterval(refreshLiveConnections, 30000);

  // Filter controls
  document
    .getElementById("activity-filter-apply")
    ?.addEventListener("click", () => {
      activityOffset = 0;
      loadActivityAudit();
    });
  document
    .getElementById("activity-filter-clear")
    ?.addEventListener("click", clearActivityFilters);

  // Pagination
  document.getElementById("activity-prev")?.addEventListener("click", () => {
    if (activityOffset >= ACTIVITY_PAGE_SIZE) {
      activityOffset -= ACTIVITY_PAGE_SIZE;
      loadActivityAudit();
    }
  });
  document.getElementById("activity-next")?.addEventListener("click", () => {
    activityOffset += ACTIVITY_PAGE_SIZE;
    loadActivityAudit();
  });

  // Refresh stats
  document
    .getElementById("refresh-activity-stats")
    ?.addEventListener("click", loadActivityStats);

  // Load data when Activity tab is clicked
  document
    .querySelector('.cabinet-tab[data-section="activity"]')
    ?.addEventListener("click", () => {
      refreshLiveConnections();
      loadActivityStats();
      loadActivityAudit();
    });
}

function clearActivityFilters() {
  const userSelect = document.getElementById("activity-filter-user");
  const typeSelect = document.getElementById("activity-filter-type");
  const fromInput = document.getElementById("activity-filter-from");
  const toInput = document.getElementById("activity-filter-to");

  if (userSelect) userSelect.value = "";
  if (typeSelect) typeSelect.value = "";
  if (fromInput) fromInput.value = "";
  if (toInput) toInput.value = "";

  activityOffset = 0;
  loadActivityAudit();
}

async function loadActivityStats() {
  try {
    const data = await safeFetch(`${API_BASE}/api/admin/activity/stats`);

    // Update stat cards
    const totalListensEl = document.getElementById("audit-total-listens");
    const totalDownloadsEl = document.getElementById("audit-total-downloads");
    const activeUsersEl = document.getElementById("audit-active-users");
    const topListenedEl = document.getElementById("audit-top-listened");

    if (totalListensEl)
      totalListensEl.textContent = (data.total_listens || 0).toLocaleString();
    if (totalDownloadsEl)
      totalDownloadsEl.textContent = (
        data.total_downloads || 0
      ).toLocaleString();
    if (activeUsersEl)
      activeUsersEl.textContent = (data.active_users || 0).toLocaleString();

    // Show top listened title if available
    if (topListenedEl) {
      if (data.top_listened && data.top_listened.length > 0) {
        const top = data.top_listened[0];
        topListenedEl.textContent = top.title || `Book #${top.audiobook_id}`;
        topListenedEl.title = `${top.count} listens`;
      } else {
        topListenedEl.textContent = "None";
      }
    }

    // Render top listened list
    renderTopList(
      "audit-top-listened-list",
      data.top_listened || [],
      "listens",
    );

    // Render top downloaded list
    renderTopList(
      "audit-top-downloaded-list",
      data.top_downloaded || [],
      "downloads",
    );

    // Populate user filter dropdown from activity data
    populateUserFilter(data);
  } catch (error) {
    console.error("Failed to load activity stats:", error);
    showToast("Failed to load activity statistics", "error");
  }
}

function renderTopList(containerId, items, label) {
  const container = document.getElementById(containerId);
  if (!container) return;

  // Clear existing content
  while (container.firstChild) {
    container.removeChild(container.firstChild);
  }

  if (!items || items.length === 0) {
    const emptyP = document.createElement("p");
    emptyP.className = "placeholder-text";
    emptyP.textContent = "No data yet";
    container.appendChild(emptyP);
    return;
  }

  items.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "audit-top-item";

    const rank = document.createElement("span");
    rank.className = "audit-top-rank";
    rank.textContent = `${index + 1}.`;

    const title = document.createElement("span");
    title.className = "audit-top-title";
    title.textContent = item.title || `Book #${item.audiobook_id}`;

    const count = document.createElement("span");
    count.className = "audit-top-count";
    count.textContent = `${item.count} ${label}`;

    row.appendChild(rank);
    row.appendChild(title);
    row.appendChild(count);
    container.appendChild(row);
  });
}

function populateUserFilter(statsData) {
  const userSelect = document.getElementById("activity-filter-user");
  if (!userSelect) return;

  // Preserve current selection
  const currentVal = userSelect.value;

  // Clear options except "All Users"
  while (userSelect.options.length > 1) {
    userSelect.remove(1);
  }

  // We need to load actual users from the admin endpoint
  // Use the auth admin users endpoint if available
  fetch(`${API_BASE}/auth/admin/users`, { credentials: "include" })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (data && data.users) {
        data.users.forEach((user) => {
          const option = document.createElement("option");
          option.value = user.id;
          option.textContent = user.username;
          userSelect.appendChild(option);
        });
      }
      // Restore selection
      if (currentVal) userSelect.value = currentVal;
    })
    .catch(() => {
      // Silently fail - filter will just show "All Users"
    });
}

async function loadActivityAudit() {
  const tbody = document.getElementById("activity-table-body");
  const badge = document.getElementById("activity-total-badge");
  const pageInfo = document.getElementById("activity-page-info");
  const prevBtn = document.getElementById("activity-prev");
  const nextBtn = document.getElementById("activity-next");

  if (!tbody) return;

  // Show loading state
  while (tbody.firstChild) {
    tbody.removeChild(tbody.firstChild);
  }
  const loadingRow = document.createElement("tr");
  const loadingCell = document.createElement("td");
  loadingCell.colSpan = 5;
  loadingCell.className = "placeholder-text";
  loadingCell.textContent = "Loading activity...";
  loadingRow.appendChild(loadingCell);
  tbody.appendChild(loadingRow);

  // Build query params from filters
  const params = new URLSearchParams();
  params.set("limit", ACTIVITY_PAGE_SIZE);
  params.set("offset", activityOffset);

  const userId = document.getElementById("activity-filter-user")?.value;
  const typeFilter = document.getElementById("activity-filter-type")?.value;
  const fromDate = document.getElementById("activity-filter-from")?.value;
  const toDate = document.getElementById("activity-filter-to")?.value;

  if (userId) params.set("user_id", userId);
  if (typeFilter) params.set("type", typeFilter);
  if (fromDate) params.set("from", fromDate);
  if (toDate) params.set("to", toDate);

  try {
    const data = await safeFetch(
      `${API_BASE}/api/admin/activity?${params.toString()}`,
    );

    // Update badge
    if (badge) badge.textContent = (data.total || 0).toLocaleString();

    // Clear tbody
    while (tbody.firstChild) {
      tbody.removeChild(tbody.firstChild);
    }

    if (!data.activity || data.activity.length === 0) {
      const emptyRow = document.createElement("tr");
      const emptyCell = document.createElement("td");
      emptyCell.colSpan = 5;
      emptyCell.className = "placeholder-text";
      emptyCell.textContent = "No activity found";
      emptyRow.appendChild(emptyCell);
      tbody.appendChild(emptyRow);
    } else {
      data.activity.forEach((item) => {
        const row = createActivityRow(item);
        tbody.appendChild(row);
      });
    }

    // Update pagination
    const currentPage = Math.floor(activityOffset / ACTIVITY_PAGE_SIZE) + 1;
    const totalPages = Math.max(
      1,
      Math.ceil((data.total || 0) / ACTIVITY_PAGE_SIZE),
    );

    if (pageInfo) pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    if (prevBtn) prevBtn.disabled = activityOffset === 0;
    if (nextBtn)
      nextBtn.disabled =
        activityOffset + ACTIVITY_PAGE_SIZE >= (data.total || 0);
  } catch (error) {
    console.error("Failed to load activity audit:", error);

    while (tbody.firstChild) {
      tbody.removeChild(tbody.firstChild);
    }
    const errorRow = document.createElement("tr");
    const errorCell = document.createElement("td");
    errorCell.colSpan = 5;
    errorCell.className = "placeholder-text";
    errorCell.textContent = "Failed to load activity";
    errorRow.appendChild(errorCell);
    tbody.appendChild(errorRow);

    showToast("Failed to load activity log", "error");
  }
}

function createActivityRow(item) {
  const row = document.createElement("tr");
  row.className = `activity-row activity-type-${item.type}`;

  // Date column
  const dateCell = document.createElement("td");
  dateCell.className = "audit-cell-date";
  dateCell.textContent = formatActivityDate(item.timestamp);
  dateCell.title = item.timestamp || "";

  // User column
  const userCell = document.createElement("td");
  userCell.className = "audit-cell-user";
  userCell.textContent = item.username || `User #${item.user_id}`;

  // Action type column
  const typeCell = document.createElement("td");
  typeCell.className = "audit-cell-type";
  const typeBadge = document.createElement("span");
  typeBadge.className = `audit-type-badge audit-type-${item.type}`;
  typeBadge.textContent = item.type === "listen" ? "Listen" : "Download";
  typeCell.appendChild(typeBadge);

  // Book title column
  const bookCell = document.createElement("td");
  bookCell.className = "audit-cell-book";
  bookCell.textContent = item.title || `Book #${item.audiobook_id}`;

  // Details column
  const detailsCell = document.createElement("td");
  detailsCell.className = "audit-cell-details";
  if (item.type === "listen" && item.duration_listened_ms) {
    detailsCell.textContent = formatDurationMs(item.duration_listened_ms);
  } else if (item.type === "download" && item.file_format) {
    detailsCell.textContent = item.file_format;
  } else {
    detailsCell.textContent = "-";
  }

  row.appendChild(dateCell);
  row.appendChild(userCell);
  row.appendChild(typeCell);
  row.appendChild(bookCell);
  row.appendChild(detailsCell);

  return row;
}

function formatActivityDate(timestamp) {
  if (!timestamp) return "-";
  try {
    const d = new Date(timestamp);
    if (isNaN(d.getTime())) return timestamp;

    const now = new Date();
    const diffMs = now - d;
    const diffHours = diffMs / 3600000;

    // Show relative time for recent activity
    if (diffHours < 24) {
      return formatRelativeTime(d);
    }

    // Show date for older activity
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: d.getFullYear() !== now.getFullYear() ? "numeric" : undefined,
    });
  } catch (e) {
    return timestamp;
  }
}

function formatDurationMs(ms) {
  if (!ms || ms <= 0) return "-";
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

// ============================================
// System Administration Section
// ============================================

let upgradePollingInterval = null;

function initSystemSection() {
  // Refresh services button
  document
    .getElementById("refresh-services")
    ?.addEventListener("click", loadServicesStatus);

  // Start/Stop all buttons
  document
    .getElementById("start-all-services")
    ?.addEventListener("click", startAllServices);
  document
    .getElementById("stop-all-services")
    ?.addEventListener("click", stopAllServices);
  document
    .getElementById("stop-background-services")
    ?.addEventListener("click", stopBackgroundServices);

  // Upgrade source toggle
  document.querySelectorAll('input[name="upgrade-source"]').forEach((radio) => {
    radio.addEventListener("change", (e) => {
      const projectSelector = document.getElementById("project-selector");
      if (projectSelector) {
        projectSelector.style.display =
          e.target.value === "project" ? "block" : "none";
      }
      const versionGroup = document.getElementById("upgrade-version-group");
      if (versionGroup) {
        versionGroup.style.display =
          e.target.value === "github" ? "flex" : "none";
      }
      // Source changed — invalidate preflight (check was for different source)
      preflightData = null;
      preflightTimestamp = null;
      updateUpgradeButtonState();
    });
  });

  // Force checkbox toggle
  const forceCheckbox = document.getElementById("upgrade-force");
  if (forceCheckbox) {
    forceCheckbox.addEventListener("change", () => {
      const warning = document.getElementById("upgrade-force-warning");
      if (warning) {
        warning.style.display = forceCheckbox.checked ? "block" : "none";
      }
      updateUpgradeButtonState();
    });
  }

  // Browse projects button
  document
    .getElementById("browse-projects")
    ?.addEventListener("click", loadProjectsList);

  // Upgrade buttons
  document
    .getElementById("check-upgrade")
    ?.addEventListener("click", checkUpgrade);
  document
    .getElementById("start-upgrade-btn")
    ?.addEventListener("click", startUpgrade);

  // Load initial data when System tab is shown
  document
    .querySelector('.cabinet-tab[data-section="system"]')
    ?.addEventListener("click", () => {
      loadServicesStatus();
      loadVersionInfo();
    });
}

// ============================================
// Users Tab (Create User, Audit Log, Badge)
// ============================================

function initUsersSection() {
  initCreateUserForm();
  initAuditLogFilter();
  // Existing user management init (tabs, refresh, invite)
  initUserManagement();

  // Load data when users tab is shown
  document
    .querySelector('.cabinet-tab[data-section="users"]')
    ?.addEventListener("click", function() {
      loadUsers();
      loadAccessRequests();
      loadAuditLog();
      loadUnseenBadge();
    });

  // Real-time audit notifications via WebSocket
  document.addEventListener("audit-notify", function () {
    loadUnseenBadge();
    if (currentSection === "users") {
      loadAuditLog();
    }
  });
}

function initCreateUserForm() {
  var form = document.getElementById("create-user-form");
  if (!form) return;

  // Show/hide email field based on auth method
  form.querySelectorAll('input[name="auth_method"]').forEach(function(radio) {
    radio.addEventListener("change", function() {
      var emailInput = document.getElementById("new-email");
      if (this.value === "magic_link") {
        emailInput.required = true;
        emailInput.placeholder = "Required for Magic Link auth";
      } else {
        emailInput.required = false;
        emailInput.placeholder = "Optional email address";
      }
    });
  });

  form.addEventListener("submit", async function(e) {
    e.preventDefault();
    var formData = new FormData(form);
    var body = {
      username: formData.get("username"),
      email: formData.get("email") || undefined,
      auth_method: formData.get("auth_method"),
      is_admin: form.querySelector('[name="is_admin"]').checked,
      can_download: form.querySelector('[name="can_download"]').checked,
    };

    try {
      var resp = await fetch("/auth/admin/users/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      var data = await resp.json();
      if (!resp.ok) {
        alert("Error: " + (data.error || "Failed to create user"));
        return;
      }

      // Show setup data
      renderSetupData(data.setup_data, body.username, body.auth_method);
      form.reset();
      loadUsers();
      loadAuditLog();
    } catch (err) {
      alert("Network error: " + err.message);
    }
  });
}

function renderSetupData(setupData, username, authMethod) {
  var panel = document.getElementById("setup-data-panel");
  var qrContainer = document.getElementById("setup-qr-container");
  var manualKey = document.getElementById("setup-manual-key");
  var claimUrl = document.getElementById("setup-claim-url");
  var downloadBtn = document.getElementById("download-setup-btn");

  panel.hidden = false;
  qrContainer.innerHTML = "";
  manualKey.textContent = "";
  claimUrl.textContent = "";
  downloadBtn.hidden = true;

  if (authMethod === "totp" && setupData && setupData.qr_uri) {
    // Generate QR code image from URI
    var img = document.createElement("img");
    img.src = "/auth/admin/users/qr?uri=" + encodeURIComponent(setupData.qr_uri);
    img.alt = "TOTP QR Code";
    img.style.maxWidth = "200px";
    img.id = "setup-qr-img";
    qrContainer.appendChild(img);

    manualKey.textContent = "Manual key: " + setupData.manual_key;

    downloadBtn.hidden = false;
    downloadBtn.onclick = function() {
      downloadQrPng(username, img);
    };
  } else if (authMethod === "passkey" && setupData && setupData.claim_url) {
    var claimHtml = document.createElement("span");
    var strong = document.createElement("strong");
    strong.textContent = "Claim URL: ";
    claimHtml.appendChild(strong);
    var code = document.createElement("code");
    code.textContent = setupData.claim_url;
    claimHtml.appendChild(code);
    claimUrl.appendChild(claimHtml);

    var copyBtn = document.createElement("button");
    copyBtn.className = "btn btn-secondary";
    copyBtn.textContent = "Copy URL";
    copyBtn.title = "Copy claim URL to clipboard";
    copyBtn.onclick = function() {
      navigator.clipboard.writeText(window.location.origin + setupData.claim_url);
      copyBtn.textContent = "Copied!";
      setTimeout(function() { copyBtn.textContent = "Copy URL"; }, 2000);
    };
    claimUrl.appendChild(copyBtn);
  } else if (authMethod === "magic_link") {
    claimUrl.textContent = "Magic Link user created. They will receive a login link via email.";
  }
}

function downloadQrPng(username, imgElement) {
  var now = new Date();
  var mmdd = String(now.getMonth() + 1).padStart(2, "0") +
             String(now.getDate()).padStart(2, "0");
  var hms = String(now.getHours()).padStart(2, "0") +
            String(now.getMinutes()).padStart(2, "0") +
            String(now.getSeconds()).padStart(2, "0");
  var filename = username + "_" + mmdd + "-" + hms + ".png";

  var canvas = document.createElement("canvas");
  canvas.width = imgElement.naturalWidth || 200;
  canvas.height = imgElement.naturalHeight || 200;
  var ctx = canvas.getContext("2d");
  ctx.drawImage(imgElement, 0, 0);

  var link = document.createElement("a");
  link.download = filename;
  link.href = canvas.toDataURL("image/png");
  link.click();
}

function initAuditLogFilter() {
  var filter = document.getElementById("audit-action-filter");
  if (!filter) return;
  filter.addEventListener("change", function() {
    loadAuditLog(this.value, 0);
  });
}

var auditPageSize = 25;

async function loadAuditLog(actionFilter, offset) {
  actionFilter = actionFilter || "";
  offset = offset || 0;

  var params = new URLSearchParams({ limit: auditPageSize, offset: offset });
  if (actionFilter) params.set("action", actionFilter);

  try {
    var resp = await fetch("/auth/admin/audit-log?" + params.toString(), {
      credentials: "same-origin",
    });
    if (!resp.ok) return;
    var data = await resp.json();

    var tbody = document.getElementById("audit-table-body");
    tbody.innerHTML = "";

    if (!data.entries || data.entries.length === 0) {
      var emptyRow = document.createElement("tr");
      var emptyCell = document.createElement("td");
      emptyCell.colSpan = 5;
      emptyCell.className = "placeholder-text";
      emptyCell.textContent = "No audit log entries";
      emptyRow.appendChild(emptyCell);
      tbody.appendChild(emptyRow);
      return;
    }

    data.entries.forEach(function(entry) {
      var tr = document.createElement("tr");
      var details = typeof entry.details === "string" ? JSON.parse(entry.details) : entry.details;
      var isCritical = ["change_username", "switch_auth_method", "reset_credentials", "delete_account"].indexOf(entry.action) >= 0;

      if (isCritical) tr.classList.add("audit-critical");

      var tdTime = document.createElement("td");
      tdTime.textContent = formatTimestamp(entry.timestamp);
      tr.appendChild(tdTime);

      var tdActor = document.createElement("td");
      tdActor.textContent = (details && details.actor_username) ? details.actor_username : "System";
      tr.appendChild(tdActor);

      var tdTarget = document.createElement("td");
      tdTarget.textContent = (details && details.target_username) ? details.target_username : "-";
      tr.appendChild(tdTarget);

      var tdAction = document.createElement("td");
      tdAction.textContent = formatAction(entry.action);
      tr.appendChild(tdAction);

      var tdDetails = document.createElement("td");
      tdDetails.textContent = formatAuditDetailsText(entry.action, details);
      tr.appendChild(tdDetails);

      tbody.appendChild(tr);
    });

    renderAuditPagination(data.total, offset, actionFilter);
  } catch (err) {
    console.error("Failed to load audit log:", err);
  }
}

function formatAction(action) {
  var map = {
    create_user: "Created User",
    change_username: "Username Changed",
    change_email: "Email Changed",
    switch_auth_method: "Auth Method Switched",
    reset_credentials: "Credentials Reset",
    toggle_roles: "Roles Changed",
    change_download: "Download Permission Changed",
    delete_account: "Account Deleted",
    delete_user: "User Deleted",
  };
  return map[action] || action;
}

function formatAuditDetailsText(action, details) {
  if (!details) return "-";
  if (details.old !== undefined && details.new !== undefined) {
    return String(details.old) + " \u2192 " + String(details.new);
  }
  if (details.auth_method) return details.auth_method;
  if (details.username) return details.username;
  return "-";
}

function formatTimestamp(ts) {
  if (!ts) return "-";
  try {
    var d = new Date(ts);
    return d.toLocaleString();
  } catch (e) {
    return ts;
  }
}

function renderAuditPagination(total, currentOffset, actionFilter) {
  var container = document.getElementById("audit-pagination");
  if (!container) return;
  container.innerHTML = "";

  var totalPages = Math.ceil(total / auditPageSize);
  var currentPage = Math.floor(currentOffset / auditPageSize);

  if (totalPages <= 1) return;

  for (var i = 0; i < totalPages && i < 10; i++) {
    var btn = document.createElement("button");
    btn.className = "pagination-btn" + (i === currentPage ? " active" : "");
    btn.textContent = i + 1;
    btn.title = "Page " + (i + 1);
    (function(pageIdx) {
      btn.onclick = function() {
        loadAuditLog(actionFilter, pageIdx * auditPageSize);
      };
    })(i);
    container.appendChild(btn);
  }
}

async function loadUnseenBadge() {
  try {
    var resp = await fetch("/auth/admin/audit-log?limit=1&offset=0", {
      credentials: "same-origin",
    });
    if (!resp.ok) return;
    var data = await resp.json();
    var badge = document.getElementById("users-badge");
    if (!badge) return;

    // For now show total count; unseen tracking requires last_audit_seen_id
    // which is a future enhancement
    if (data.total > 0) {
      badge.textContent = data.total;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  } catch (err) {
    // Ignore
  }
}

// ============================================
// User Management
// ============================================

function initUserManagement() {
  // Tab switching
  document.querySelectorAll(".user-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabName = tab.dataset.tab;
      document
        .querySelectorAll(".user-tab")
        .forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");

      document.querySelectorAll(".user-tab-content").forEach((content) => {
        content.hidden = content.id !== `${tabName}-tab`;
        content.classList.toggle("active", content.id === `${tabName}-tab`);
      });
    });
  });

  // Refresh users button
  document.getElementById("refresh-users")?.addEventListener("click", () => {
    loadUsers();
    loadAccessRequests();
  });

  // Invite user button
  document
    .getElementById("invite-user-btn")
    ?.addEventListener("click", showInviteUserModal);
}

function showInviteUserModal() {
  const modal = document.getElementById("invite-user-modal");
  const usernameEl = document.getElementById("invite-username");
  const emailEl = document.getElementById("invite-email");
  const canDownloadEl = document.getElementById("invite-can-download");
  const authMethodEl = document.getElementById("invite-auth-method");
  const authHintEl = document.getElementById("invite-auth-hint");
  const sendBtn = document.getElementById("invite-user-send");
  const cancelBtn = document.getElementById("invite-user-cancel");
  const closeBtn = document.getElementById("invite-user-close");

  const authHints = {
    magic_link:
      "User clicks a link in their email to sign in \u2014 no codes or apps needed",
    totp: "User sets up an authenticator app (Google Authenticator, Authy, etc.) on their phone",
    passkey:
      "User registers a passkey or physical security key in their browser",
  };

  // Reset form
  usernameEl.value = "";
  emailEl.value = "";
  canDownloadEl.checked = true;
  authMethodEl.value = "magic_link";
  authHintEl.textContent = authHints.magic_link;

  // Update hint when auth method changes
  authMethodEl.onchange = () => {
    authHintEl.textContent = authHints[authMethodEl.value] || "";
  };

  modal.classList.add("active");

  // Clone buttons to remove old event listeners
  const newSendBtn = sendBtn.cloneNode(true);
  const newCancelBtn = cancelBtn.cloneNode(true);
  const newCloseBtn = closeBtn.cloneNode(true);
  sendBtn.parentNode.replaceChild(newSendBtn, sendBtn);
  cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
  closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);

  const closeModal = () => modal.classList.remove("active");

  newCancelBtn.addEventListener("click", closeModal);
  newCloseBtn.addEventListener("click", closeModal);

  newSendBtn.addEventListener("click", async () => {
    const username = usernameEl.value.trim();
    const email = emailEl.value.trim();
    const canDownload = canDownloadEl.checked;
    const authMethod = authMethodEl.value;

    // Validate
    if (!username || username.length < 3) {
      showToast("Username must be at least 3 characters", "error");
      return;
    }
    if (username.length > 24) {
      showToast("Username must be at most 24 characters", "error");
      return;
    }
    if (/[<>\\]/.test(username)) {
      showToast("Username cannot contain < > or \\ characters", "error");
      return;
    }
    if (!email) {
      showToast("Email address is required", "error");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      showToast("Invalid email format", "error");
      return;
    }

    // Disable send button
    newSendBtn.disabled = true;
    newSendBtn.textContent = "Sending...";

    try {
      const res = await fetch(`${API_BASE}/auth/admin/users/invite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          username: username,
          email: email,
          can_download: canDownload,
          auth_method: authMethod,
        }),
      });

      const data = await res.json();

      if (res.ok) {
        closeModal();
        if (authMethod === "magic_link") {
          if (data.email_sent) {
            showToast(`Magic link invitation sent to ${email}`, "success");
          } else {
            showToast(
              `User created but email failed. Admin can resend from user management.`,
              "warning",
            );
          }
        } else {
          if (data.email_sent) {
            showToast(`Invitation sent to ${email}`, "success");
          } else {
            showToast(
              `User created. Email failed \u2014 claim token: ${data.claim_token}`,
              "warning",
            );
          }
        }
        loadUsers();
      } else {
        showToast(data.error || "Failed to invite user", "error");
      }
    } catch (error) {
      showToast("Connection error", "error");
    } finally {
      newSendBtn.disabled = false;
      newSendBtn.textContent = "Send Invitation";
    }
  });
}

async function loadUsers() {
  const userList = document.getElementById("user-list");
  const countBadge = document.getElementById("users-count-badge");
  if (!userList) return;

  // Clear and show loading
  while (userList.firstChild) {
    userList.removeChild(userList.firstChild);
  }
  const loadingP = document.createElement("p");
  loadingP.className = "placeholder-text";
  loadingP.textContent = "Loading users...";
  userList.appendChild(loadingP);

  try {
    const res = await fetch(`${API_BASE}/auth/admin/users`, {
      credentials: "include",
    });

    if (!res.ok) {
      throw new Error("Failed to load users");
    }

    const data = await res.json();

    // Clear loading
    while (userList.firstChild) {
      userList.removeChild(userList.firstChild);
    }

    if (!data.users || data.users.length === 0) {
      const emptyP = document.createElement("p");
      emptyP.className = "empty-message";
      emptyP.textContent = "No users found";
      userList.appendChild(emptyP);
      if (countBadge) countBadge.textContent = "0";
      return;
    }

    if (countBadge) countBadge.textContent = data.users.length;

    data.users.forEach((user) => {
      const item = createUserItem(user);
      userList.appendChild(item);
    });
  } catch (error) {
    console.error("Error loading users:", error);
    while (userList.firstChild) {
      userList.removeChild(userList.firstChild);
    }
    const errorP = document.createElement("p");
    errorP.className = "placeholder-text";
    errorP.textContent = "Failed to load users";
    userList.appendChild(errorP);
  }
}

function createUserItem(user) {
  const item = document.createElement("div");
  item.className = "user-item";
  item.dataset.userId = user.id;

  // User info
  const info = document.createElement("div");
  info.className = "user-info";

  const nameRow = document.createElement("div");
  nameRow.className = "user-name";
  nameRow.textContent = user.username;

  const badges = document.createElement("div");
  badges.className = "user-badges";

  if (user.is_admin) {
    const adminBadge = document.createElement("span");
    adminBadge.className = "user-badge admin";
    adminBadge.textContent = "Admin";
    badges.appendChild(adminBadge);
  }

  const downloadBadge = document.createElement("span");
  downloadBadge.className = `user-badge ${user.can_download ? "download" : "no-download"}`;
  downloadBadge.textContent = user.can_download ? "Download" : "No Download";
  badges.appendChild(downloadBadge);

  nameRow.appendChild(badges);
  info.appendChild(nameRow);

  const meta = document.createElement("div");
  meta.className = "user-meta";
  const emailInfo = user.email ? ` • ${user.email}` : "";
  if (user.last_login) {
    meta.textContent = `Last login: ${new Date(user.last_login).toLocaleString()}${emailInfo}`;
  } else if (user.created_at) {
    let metaText = `Invited: ${new Date(user.created_at).toLocaleString()} UTC${emailInfo}`;
    meta.textContent = metaText;
    if (user.invite_expires_at) {
      const br = document.createElement("br");
      meta.appendChild(br);
      const expirySpan = document.createElement("span");
      if (user.invite_expired) {
        expirySpan.style.color = "var(--color-danger, #e74c3c)";
        expirySpan.textContent = `Expired: ${new Date(user.invite_expires_at).toLocaleString()} UTC`;
      } else {
        expirySpan.style.color = "var(--color-warning, #f39c12)";
        expirySpan.textContent = `Expires: ${new Date(user.invite_expires_at).toLocaleString()} UTC`;
      }
      meta.appendChild(expirySpan);
    }
  } else {
    meta.textContent = `Last login: Never${emailInfo}`;
  }
  info.appendChild(meta);

  item.appendChild(info);

  // Actions
  const actions = document.createElement("div");
  actions.className = "user-actions";

  // Edit user
  const editBtn = document.createElement("button");
  editBtn.className = "user-action-btn edit";
  editBtn.textContent = "Edit";
  editBtn.title = `Edit ${user.username}`;
  editBtn.addEventListener("click", () => showEditUserModal(user));
  actions.appendChild(editBtn);

  // Toggle admin status (not for self - can't remove your own admin)
  const adminBtn = document.createElement("button");
  adminBtn.className = `user-action-btn ${user.is_admin ? "revoke-admin" : "grant-admin"}`;
  adminBtn.textContent = user.is_admin ? "Revoke Admin" : "Make Admin";
  adminBtn.title = user.is_admin
    ? `Remove admin privileges from ${user.username}`
    : `Grant admin privileges to ${user.username}`;
  adminBtn.addEventListener("click", () => toggleUserAdmin(user));
  actions.appendChild(adminBtn);

  // Toggle download permission
  const toggleBtn = document.createElement("button");
  toggleBtn.className = "user-action-btn toggle";
  toggleBtn.textContent = user.can_download
    ? "Disable Download"
    : "Enable Download";
  toggleBtn.title = `Toggle download permission for ${user.username}`;
  toggleBtn.addEventListener("click", () =>
    toggleUserDownload(user.id, !user.can_download),
  );
  actions.appendChild(toggleBtn);

  // Delete user (not for admins)
  if (!user.is_admin) {
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "user-action-btn delete";
    deleteBtn.textContent = "Delete";
    deleteBtn.title = `Delete user ${user.username}`;
    deleteBtn.addEventListener("click", () => confirmDeleteUser(user));
    actions.appendChild(deleteBtn);
  }

  item.appendChild(actions);
  return item;
}

async function toggleUserDownload(userId, canDownload) {
  try {
    const res = await fetch(
      `${API_BASE}/auth/admin/users/${userId}/toggle-download`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
      },
    );

    if (res.ok) {
      showToast(
        `Download permission ${canDownload ? "enabled" : "disabled"}`,
        "success",
      );
      loadUsers();
    } else {
      const data = await res.json();
      showToast(data.error || "Failed to update permission", "error");
    }
  } catch (error) {
    showToast("Connection error", "error");
  }
}

async function toggleUserAdmin(user) {
  // Confirm the action
  const action = user.is_admin
    ? "revoke admin privileges from"
    : "grant admin privileges to";
  const confirmed = await new Promise((resolve) => {
    showConfirmModal(
      user.is_admin ? "Revoke Admin" : "Grant Admin",
      `Are you sure you want to ${action} "${user.username}"?`,
      () => resolve(true),
      () => resolve(false),
    );
  });

  if (!confirmed) return;

  try {
    const res = await fetch(
      `${API_BASE}/auth/admin/users/${user.id}/toggle-admin`,
      {
        method: "POST",
        credentials: "include",
      },
    );

    if (res.ok) {
      const data = await res.json();
      const status = data.is_admin ? "granted" : "revoked";
      showToast(`Admin privileges ${status} for ${user.username}`, "success");
      loadUsers();
    } else {
      const data = await res.json();
      showToast(data.error || "Failed to update admin status", "error");
    }
  } catch (error) {
    showToast("Connection error", "error");
  }
}

function confirmDeleteUser(user) {
  showConfirmModal(
    "Delete User",
    `Are you sure you want to delete user "${user.username}"? This action cannot be undone.`,
    async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/admin/users/${user.id}/delete`, {
          method: "DELETE",
          credentials: "include",
        });

        if (res.ok) {
          showToast(`User ${user.username} deleted`, "success");
          loadUsers();
        } else {
          const data = await res.json();
          showToast(data.error || "Failed to delete user", "error");
        }
      } catch (error) {
        showToast("Connection error", "error");
      }
    },
  );
}

function showEditUserModal(user, isProfile = false) {
  const modal = document.getElementById("edit-user-modal");
  const titleEl = document.getElementById("edit-user-title");
  const userIdEl = document.getElementById("edit-user-id");
  const usernameEl = document.getElementById("edit-username");
  const emailEl = document.getElementById("edit-email");
  const saveBtn = document.getElementById("edit-user-save");
  const cancelBtn = document.getElementById("edit-user-cancel");
  const closeBtn = document.getElementById("edit-user-close");

  titleEl.textContent = isProfile
    ? "Edit Profile"
    : `Edit User: ${user.username}`;
  userIdEl.value = user.id;
  usernameEl.value = user.username;
  emailEl.value = user.email || "";

  modal.classList.add("active");

  // Clone buttons to remove old event listeners
  const newSaveBtn = saveBtn.cloneNode(true);
  const newCancelBtn = cancelBtn.cloneNode(true);
  const newCloseBtn = closeBtn.cloneNode(true);
  saveBtn.parentNode.replaceChild(newSaveBtn, saveBtn);
  cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
  closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);

  const closeModal = () => modal.classList.remove("active");

  newCancelBtn.addEventListener("click", closeModal);
  newCloseBtn.addEventListener("click", closeModal);

  newSaveBtn.addEventListener("click", async () => {
    const newUsername = usernameEl.value.trim();
    const newEmail = emailEl.value.trim();

    if (!newUsername || newUsername.length < 3) {
      showToast("Username must be at least 3 characters", "error");
      return;
    }
    if (newUsername.length > 24) {
      showToast("Username must be at most 24 characters", "error");
      return;
    }
    // Check for invalid characters
    if (/[<>\\]/.test(newUsername)) {
      showToast("Username cannot contain < > or \\ characters", "error");
      return;
    }
    if (newUsername !== newUsername.trim()) {
      showToast("Username cannot have leading or trailing spaces", "error");
      return;
    }
    // Validate email if provided
    if (newEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newEmail)) {
      showToast("Invalid email format", "error");
      return;
    }

    try {
      // Use granular audited endpoints for each changed field
      var errors = [];
      var usernameBase = isProfile
        ? `${API_BASE}/auth/account`
        : `${API_BASE}/auth/admin/users/${user.id}`;

      // Update username if changed
      if (newUsername !== user.username) {
        var uRes = await fetch(`${usernameBase}/username`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ username: newUsername }),
        });
        if (!uRes.ok) {
          var uErr = await uRes.json().catch(function () { return {}; });
          errors.push(uErr.error || "Failed to update username");
        }
      }

      // Update email if changed
      var currentEmail = user.email || user.recovery_email || "";
      if (newEmail !== currentEmail) {
        var eRes = await fetch(`${usernameBase}/email`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ email: newEmail || null }),
        });
        if (!eRes.ok) {
          var eErr = await eRes.json().catch(function () { return {}; });
          errors.push(eErr.error || "Failed to update email");
        }
      }

      if (errors.length === 0) {
        showToast("User updated successfully", "success");
        closeModal();
        if (isProfile) {
          var usernameDisplay = document.getElementById("username-display");
          if (usernameDisplay) usernameDisplay.textContent = newUsername;
        } else {
          loadUsers();
        }
      } else {
        showToast(errors.join("; "), "error");
      }
    } catch (error) {
      showToast("Connection error", "error");
    }
  });
}

async function loadAccessRequests() {
  const requestsList = document.getElementById("requests-list");
  const pendingBadge = document.getElementById("pending-requests-count");
  if (!requestsList) return;

  // Clear and show loading
  while (requestsList.firstChild) {
    requestsList.removeChild(requestsList.firstChild);
  }
  const loadingP = document.createElement("p");
  loadingP.className = "placeholder-text";
  loadingP.textContent = "Loading requests...";
  requestsList.appendChild(loadingP);

  try {
    const res = await fetch(`${API_BASE}/auth/admin/access-requests`, {
      credentials: "include",
    });

    if (!res.ok) {
      throw new Error("Failed to load requests");
    }

    const data = await res.json();

    // Clear loading
    while (requestsList.firstChild) {
      requestsList.removeChild(requestsList.firstChild);
    }

    // Count pending
    const pendingCount = data.requests.filter(
      (r) => r.status === "pending",
    ).length;
    if (pendingBadge) {
      pendingBadge.textContent = pendingCount;
      pendingBadge.hidden = pendingCount === 0;
    }

    if (!data.requests || data.requests.length === 0) {
      const emptyP = document.createElement("p");
      emptyP.className = "empty-message";
      emptyP.textContent = "No access requests";
      requestsList.appendChild(emptyP);
      return;
    }

    data.requests.forEach((req) => {
      const item = createRequestItem(req);
      requestsList.appendChild(item);
    });
  } catch (error) {
    console.error("Error loading requests:", error);
    while (requestsList.firstChild) {
      requestsList.removeChild(requestsList.firstChild);
    }
    const errorP = document.createElement("p");
    errorP.className = "placeholder-text";
    errorP.textContent = "Failed to load requests";
    requestsList.appendChild(errorP);
  }
}

function createRequestItem(req) {
  const item = document.createElement("div");
  item.className = "request-item";
  item.dataset.requestId = req.id;

  // Request info
  const info = document.createElement("div");
  info.className = "request-info";

  const nameRow = document.createElement("div");
  nameRow.className = "request-username";
  nameRow.textContent = req.username;

  if (req.has_email) {
    const emailBadge = document.createElement("span");
    emailBadge.className = "user-badge has-email";
    emailBadge.textContent = "Has Email";
    emailBadge.title = "User provided an email for notification";
    nameRow.appendChild(emailBadge);
  }

  info.appendChild(nameRow);

  const meta = document.createElement("div");
  meta.className = "request-meta";
  const requestedAt = req.requested_at
    ? new Date(req.requested_at).toLocaleString()
    : "Unknown";
  meta.textContent = `Requested: ${requestedAt}`;
  info.appendChild(meta);

  // Status badge
  const statusDiv = document.createElement("div");
  statusDiv.className = "request-status";

  const statusBadge = document.createElement("span");
  statusBadge.className = `status-badge ${req.status}`;
  statusBadge.textContent = req.status;
  statusDiv.appendChild(statusBadge);

  if (req.status !== "pending" && req.reviewed_by) {
    const reviewMeta = document.createElement("span");
    reviewMeta.className = "request-meta";
    reviewMeta.textContent = `by ${req.reviewed_by}`;
    statusDiv.appendChild(reviewMeta);
  }

  info.appendChild(statusDiv);
  item.appendChild(info);

  // Actions (only for pending)
  if (req.status === "pending") {
    const actions = document.createElement("div");
    actions.className = "request-actions";

    const approveBtn = document.createElement("button");
    approveBtn.className = "user-action-btn approve";
    approveBtn.textContent = "Approve";
    approveBtn.title = `Approve access for ${req.username}`;
    approveBtn.addEventListener("click", () =>
      approveRequest(req.id, req.username),
    );
    actions.appendChild(approveBtn);

    const denyBtn = document.createElement("button");
    denyBtn.className = "user-action-btn deny";
    denyBtn.textContent = "Deny";
    denyBtn.title = `Deny access for ${req.username}`;
    denyBtn.addEventListener("click", () =>
      showDenyModal(req.id, req.username),
    );
    actions.appendChild(denyBtn);

    item.appendChild(actions);
  }

  return item;
}

async function approveRequest(requestId, username) {
  try {
    const res = await fetch(
      `${API_BASE}/auth/admin/access-requests/${requestId}/approve`,
      {
        method: "POST",
        credentials: "include",
      },
    );

    const data = await res.json();

    if (res.ok) {
      let msg = `Access approved for ${username}`;
      if (data.email_sent) {
        msg += " (notification email sent)";
      }
      showToast(msg, "success");
      loadUsers();
      loadAccessRequests();
    } else {
      showToast(data.error || "Failed to approve request", "error");
    }
  } catch (error) {
    showToast("Connection error", "error");
  }
}

function showDenyModal(requestId, username) {
  // Create modal content with reason textarea
  const modalBody = document.getElementById("confirm-body");
  while (modalBody.firstChild) {
    modalBody.removeChild(modalBody.firstChild);
  }

  const content = document.createElement("div");
  content.className = "user-modal-content";

  const message = document.createElement("p");
  message.textContent = `Deny access request from "${username}"?`;
  content.appendChild(message);

  const formGroup = document.createElement("div");
  formGroup.className = "form-group";

  const label = document.createElement("label");
  label.textContent = "Reason (optional):";
  formGroup.appendChild(label);

  const textarea = document.createElement("textarea");
  textarea.id = "deny-reason-input";
  textarea.placeholder = "Enter reason for denial...";
  formGroup.appendChild(textarea);

  content.appendChild(formGroup);
  modalBody.appendChild(content);

  // Update confirm button
  const confirmBtn = document.getElementById("confirm-action");
  confirmBtn.textContent = "Deny Request";
  confirmBtn.className = "office-btn danger";

  // Show modal
  const modal = document.getElementById("confirm-modal");
  const title = document.getElementById("confirm-title");
  title.textContent = "Deny Access Request";
  modal.classList.add("active");

  // Handle confirm
  const newConfirmBtn = confirmBtn.cloneNode(true);
  confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
  newConfirmBtn.addEventListener("click", async () => {
    const reason = document.getElementById("deny-reason-input")?.value.trim();
    modal.classList.remove("active");

    try {
      const res = await fetch(
        `${API_BASE}/auth/admin/access-requests/${requestId}/deny`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ reason: reason || null }),
        },
      );

      const data = await res.json();

      if (res.ok) {
        let msg = `Access denied for ${username}`;
        if (data.email_sent) {
          msg += " (notification email sent)";
        }
        showToast(msg, "success");
        loadAccessRequests();
      } else {
        showToast(data.error || "Failed to deny request", "error");
      }
    } catch (error) {
      showToast("Connection error", "error");
    }
  });
}

async function loadServicesStatus() {
  const servicesList = document.getElementById("services-list");
  const statusBadge = document.getElementById("services-status-badge");

  if (!servicesList) return;

  // Clear existing content safely
  while (servicesList.firstChild) {
    servicesList.removeChild(servicesList.firstChild);
  }

  const loadingP = document.createElement("p");
  loadingP.className = "placeholder-text";
  loadingP.textContent = "Loading services...";
  servicesList.appendChild(loadingP);

  try {
    const res = await fetch(`${API_BASE}/api/system/services`);
    const data = await res.json();

    // Clear loading message
    while (servicesList.firstChild) {
      servicesList.removeChild(servicesList.firstChild);
    }

    if (!data.services) {
      const errorP = document.createElement("p");
      errorP.className = "placeholder-text";
      errorP.textContent = "Failed to load services";
      servicesList.appendChild(errorP);
      return;
    }

    data.services.forEach((service) => {
      const serviceDiv = document.createElement("div");
      serviceDiv.className = "service-item";
      serviceDiv.dataset.service = service.name;

      // Service info section
      const infoDiv = document.createElement("div");
      infoDiv.className = "service-info";

      const indicator = document.createElement("span");
      indicator.className = `service-status-indicator ${service.active ? "active" : "inactive"}`;
      infoDiv.appendChild(indicator);

      const textDiv = document.createElement("div");

      const nameDiv = document.createElement("div");
      nameDiv.className = "service-name";
      nameDiv.textContent = service.name;
      textDiv.appendChild(nameDiv);

      const statusDiv = document.createElement("div");
      statusDiv.className = "service-status-text";
      statusDiv.textContent =
        service.status + (service.enabled ? " (enabled)" : "");
      textDiv.appendChild(statusDiv);

      infoDiv.appendChild(textDiv);
      serviceDiv.appendChild(infoDiv);

      // Controls section
      const controlsDiv = document.createElement("div");
      controlsDiv.className = "service-controls";

      if (service.active) {
        const stopBtn = document.createElement("button");
        stopBtn.className = "service-btn stop";
        stopBtn.title = "Stop";
        stopBtn.textContent = "⏹";
        stopBtn.addEventListener("click", () => stopService(service.name));
        controlsDiv.appendChild(stopBtn);

        const restartBtn = document.createElement("button");
        restartBtn.className = "service-btn restart";
        restartBtn.title = "Restart";
        restartBtn.textContent = "↻";
        restartBtn.addEventListener("click", () =>
          restartService(service.name),
        );
        controlsDiv.appendChild(restartBtn);
      } else {
        const startBtn = document.createElement("button");
        startBtn.className = "service-btn start";
        startBtn.title = "Start";
        startBtn.textContent = "▶";
        startBtn.addEventListener("click", () => startService(service.name));
        controlsDiv.appendChild(startBtn);
      }

      serviceDiv.appendChild(controlsDiv);
      servicesList.appendChild(serviceDiv);
    });

    // Update status badge
    if (statusBadge) {
      const activeCount = data.services.filter((s) => s.active).length;
      const totalCount = data.services.length;

      statusBadge.textContent = `${activeCount}/${totalCount} running`;
      statusBadge.className = "badge";

      if (activeCount === totalCount) {
        statusBadge.classList.add("all-running");
      } else if (activeCount > 0) {
        statusBadge.classList.add("partial");
      } else {
        statusBadge.classList.add("error");
      }
    }
  } catch (error) {
    console.error("Failed to load services:", error);
    while (servicesList.firstChild) {
      servicesList.removeChild(servicesList.firstChild);
    }
    const errorP = document.createElement("p");
    errorP.className = "placeholder-text";
    errorP.textContent = "Error loading services";
    servicesList.appendChild(errorP);
    showToast("Failed to load services: " + error.message, "error");
  }
}

async function startService(serviceName) {
  try {
    showToast(`Starting ${serviceName}...`, "info");
    const res = await fetch(
      `${API_BASE}/api/system/services/${serviceName}/start`,
      { method: "POST" },
    );
    const result = await res.json();

    if (result.success) {
      showToast(result.message, "success");
      loadServicesStatus();
    } else {
      showToast(result.error || "Failed to start service", "error");
    }
  } catch (error) {
    showToast("Failed to start service: " + error.message, "error");
  }
}

async function stopService(serviceName) {
  try {
    showToast(`Stopping ${serviceName}...`, "info");
    const res = await fetch(
      `${API_BASE}/api/system/services/${serviceName}/stop`,
      { method: "POST" },
    );
    const result = await res.json();

    if (result.success) {
      showToast(result.message, "success");
      loadServicesStatus();
    } else {
      showToast(result.error || "Failed to stop service", "error");
    }
  } catch (error) {
    showToast("Failed to stop service: " + error.message, "error");
  }
}

async function restartService(serviceName) {
  try {
    showToast(`Restarting ${serviceName}...`, "info");
    const res = await fetch(
      `${API_BASE}/api/system/services/${serviceName}/restart`,
      { method: "POST" },
    );
    const result = await res.json();

    if (result.success) {
      showToast(result.message, "success");
      loadServicesStatus();
    } else {
      showToast(result.error || "Failed to restart service", "error");
    }
  } catch (error) {
    showToast("Failed to restart service: " + error.message, "error");
  }
}

async function startAllServices() {
  if (
    !(await confirmAction(
      "Start All Services",
      "Start all audiobook services?",
    ))
  ) {
    return;
  }

  try {
    showToast("Starting all services...", "info");
    const res = await fetch(`${API_BASE}/api/system/services/start-all`, {
      method: "POST",
    });
    const result = await res.json();

    if (result.success) {
      showToast("All services started", "success");
    } else {
      const failures = result.results
        ?.filter((r) => !r.success)
        .map((r) => r.service)
        .join(", ");
      showToast(`Some services failed to start: ${failures}`, "error");
    }
    loadServicesStatus();
  } catch (error) {
    showToast("Failed to start services: " + error.message, "error");
  }
}

async function stopAllServices() {
  if (
    !(await confirmAction(
      "Stop All Services",
      "This will stop ALL services including the API. You will lose web access and need to restart services via command line. Continue?",
    ))
  ) {
    return;
  }

  try {
    showToast("Stopping all services...", "info");
    const res = await fetch(
      `${API_BASE}/api/system/services/stop-all?include_api=true`,
      { method: "POST" },
    );
    const result = await res.json();

    if (result.success) {
      showToast(
        "All services stopped. Web access will be lost shortly.",
        "success",
      );
    } else {
      showToast("Some services failed to stop", "error");
    }
    // Don't refresh - we're about to lose connection
  } catch (error) {
    // Expected if API stopped before response
    showToast("Services stopping... connection lost as expected.", "info");
  }
}

async function stopBackgroundServices() {
  if (
    !(await confirmAction(
      "Stop Background Services",
      "Stop converter, mover, and scanner services? API and proxy will remain running for web access.",
    ))
  ) {
    return;
  }

  try {
    showToast("Stopping background services...", "info");
    const res = await fetch(`${API_BASE}/api/system/services/stop-all`, {
      method: "POST",
    });
    const result = await res.json();

    if (result.success) {
      showToast("Background services stopped", "success");
    } else {
      showToast("Some services failed to stop", "error");
    }
    loadServicesStatus();
  } catch (error) {
    showToast("Failed to stop services: " + error.message, "error");
  }
}

async function loadVersionInfo() {
  try {
    const res = await fetch(`${API_BASE}/api/system/version`);
    const data = await res.json();

    const versionEl = document.getElementById("current-version");
    const pathEl = document.getElementById("install-path");

    if (versionEl) versionEl.textContent = data.version || "unknown";
    if (pathEl) pathEl.textContent = data.project_root || "-";
  } catch (error) {
    console.error("Failed to load version:", error);
  }
}

async function loadProjectsList() {
  const projectsList = document.getElementById("available-projects");
  const pathInput = document.getElementById("project-path-input");

  if (!projectsList) return;

  try {
    const res = await fetch(`${API_BASE}/api/system/projects`);
    const data = await res.json();

    // Clear existing content
    while (projectsList.firstChild) {
      projectsList.removeChild(projectsList.firstChild);
    }

    if (!data.projects || data.projects.length === 0) {
      const noProjects = document.createElement("p");
      noProjects.className = "placeholder-text";
      noProjects.textContent = "No projects found";
      projectsList.appendChild(noProjects);
      projectsList.style.display = "block";
      return;
    }

    projectsList.style.display = "block";

    data.projects.forEach((project) => {
      const optionDiv = document.createElement("div");
      optionDiv.className = "project-option";

      const infoDiv = document.createElement("div");

      const nameDiv = document.createElement("div");
      nameDiv.className = "project-name";
      nameDiv.textContent = project.name;
      infoDiv.appendChild(nameDiv);

      const pathDiv = document.createElement("div");
      pathDiv.className = "project-path";
      pathDiv.textContent = project.path;
      infoDiv.appendChild(pathDiv);

      optionDiv.appendChild(infoDiv);

      const versionDiv = document.createElement("div");
      versionDiv.className = "project-version";
      versionDiv.textContent = project.version || "-";
      optionDiv.appendChild(versionDiv);

      optionDiv.addEventListener("click", () => {
        if (pathInput) pathInput.value = project.path;
        document
          .querySelectorAll(".project-option")
          .forEach((opt) => opt.classList.remove("selected"));
        optionDiv.classList.add("selected");
      });

      projectsList.appendChild(optionDiv);
    });
  } catch (error) {
    console.error("Failed to load projects:", error);
    showToast("Failed to load projects: " + error.message, "error");
  }
}

// ============================================
// Upgrade Preflight State
// ============================================

let preflightData = null;
let preflightTimestamp = null;

function updateUpgradeButtonState() {
  const startBtn = document.getElementById("start-upgrade-btn");
  const forceCheckbox = document.getElementById("upgrade-force");
  if (!startBtn) return;

  if (forceCheckbox && forceCheckbox.checked) {
    startBtn.disabled = false;
    startBtn.title = "Force upgrade \u2014 safety checks bypassed";
    return;
  }

  if (!preflightData || !preflightTimestamp) {
    startBtn.disabled = true;
    startBtn.title = "Run 'Check for Updates' first";
    return;
  }

  const ageMinutes = (Date.now() - preflightTimestamp) / 60000;
  if (ageMinutes > 10) {
    startBtn.disabled = true;
    startBtn.title = "Preflight check is stale \u2014 run Check for Updates again";
    return;
  }

  startBtn.disabled = false;
  startBtn.title = "Start upgrade";
}

async function checkUpgrade() {
  const sourceRadio = document.querySelector(
    'input[name="upgrade-source"]:checked',
  );
  const source = sourceRadio?.value || "github";
  const projectPath = document.getElementById("project-path-input")?.value;

  if (source === "project" && !projectPath) {
    showToast("Please enter or select a project directory", "error");
    return;
  }

  // Show progress modal
  showProgressModal("Checking for Updates", "Running dry-run check...");

  try {
    const body = { source };
    if (source === "project") {
      body.project_path = projectPath;
    }
    const versionInput = document.getElementById("upgrade-version");
    if (versionInput && versionInput.value.trim() && source === "github") {
      body.version = versionInput.value.trim();
    }

    const res = await fetch(`${API_BASE}/api/system/upgrade/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const result = await res.json();

    if (result.success) {
      // Poll for check results
      startCheckPolling();
    } else {
      hideProgressModal();
      showToast(result.error || "Failed to start check", "error");
    }
  } catch (error) {
    hideProgressModal();
    showToast("Failed to check for updates: " + error.message, "error");
  }
}

function startCheckPolling() {
  // Poll check status every 1 second (check is faster than upgrade)
  const CHECK_TIMEOUT_MS = 30000; // 30 second timeout for check operations
  const checkStartTime = Date.now();

  // Allow Escape key to dismiss a stuck modal
  const escapeHandler = function (e) {
    if (e.key === "Escape") {
      clearInterval(checkPollingInterval);
      document.removeEventListener("keydown", escapeHandler);
      hideProgressModal();
      showToast("Check cancelled", "info");
    }
  };
  document.addEventListener("keydown", escapeHandler);

  const checkPollingInterval = setInterval(async () => {
    try {
      // Timeout: if check takes too long, the helper likely crashed
      if (Date.now() - checkStartTime > CHECK_TIMEOUT_MS) {
        clearInterval(checkPollingInterval);
        document.removeEventListener("keydown", escapeHandler);
        const messageEl = document.getElementById("progress-message");
        if (messageEl) {
          messageEl.textContent =
            "✗ Check timed out — upgrade helper may have failed";
          messageEl.style.color = "var(--accent-red, #c0392b)";
        }
        const closeBtn = document.getElementById("modal-close-progress");
        if (closeBtn) {
          closeBtn.style.display = "inline-flex";
        }
        showToast(
          "Check timed out. Check system logs: journalctl -u audiobook-upgrade-helper.service",
          "error",
        );
        return;
      }

      const res = await fetch(`${API_BASE}/api/system/upgrade/status`);
      const status = await res.json();

      // Update progress modal
      const messageEl = document.getElementById("progress-message");
      const outputEl = document.getElementById("progress-output");

      if (messageEl) {
        messageEl.textContent = status.message || "Checking...";
      }

      if (outputEl && status.output) {
        outputEl.textContent = status.output.join("\n");
        outputEl.scrollTop = outputEl.scrollHeight;
      }

      // Handle completion
      if (!status.running && status.success !== null) {
        clearInterval(checkPollingInterval);
        document.removeEventListener("keydown", escapeHandler);

        // Show close button
        const closeBtn = document.getElementById("modal-close-progress");
        if (closeBtn) {
          closeBtn.style.display = "inline-flex";
        }

        // Store preflight data for button state gating
        preflightData = status.result || status;
        preflightTimestamp = Date.now();
        updateUpgradeButtonState();

        // Update message based on result
        if (status.result?.upgrade_available) {
          const current = status.result.current_version || "?";
          const available = status.result.available_version || "?";
          if (messageEl) {
            messageEl.textContent = `▲ Upgrade available: ${current} → ${available}`;
            messageEl.style.color = "var(--accent-gold)";
          }
          showToast(`Upgrade available: ${current} → ${available}`, "success");
        } else {
          if (messageEl) {
            messageEl.textContent = "✓ Already up to date";
            messageEl.style.color = "var(--accent-green)";
          }
          showToast("Already up to date", "success");
        }
      }
    } catch (error) {
      clearInterval(checkPollingInterval);
      document.removeEventListener("keydown", escapeHandler);
      hideProgressModal();
      showToast("Error checking status: " + error.message, "error");
    }
  }, 1000);
}

async function startUpgrade() {
  const sourceRadio = document.querySelector(
    'input[name="upgrade-source"]:checked',
  );
  const source = sourceRadio?.value || "github";
  const projectPath = document.getElementById("project-path-input")?.value;
  const forceChecked = document.getElementById("upgrade-force")?.checked || false;
  const majorChecked = document.getElementById("upgrade-major")?.checked || false;

  if (source === "project" && !projectPath) {
    showToast("Please enter or select a project directory", "error");
    return;
  }

  // Single confirmation — danger-styled for force, normal for regular
  const message = forceChecked
    ? "FORCE UPGRADE: This bypasses all safety checks including preflight validation, version comparison, and compatibility checks.\n\nThe installation backup will still be created.\n\nOnly proceed if you have a specific technical reason."
    : source === "github"
      ? "This will download and install the latest version from GitHub. The browser will reload when complete. Continue?"
      : "This will install from the project directory. The browser will reload when complete. Continue?";

  const title = forceChecked ? "Force Upgrade — Safety Checks Bypassed" : "Start Upgrade";

  if (!(await confirmAction(title, message))) {
    return;
  }

  // Build request body with advanced options
  const body = {
    source,
    force: forceChecked,
    major_version: majorChecked,
  };
  if (source === "project") {
    body.project_path = projectPath;
  }
  const versionInput = document.getElementById("upgrade-version");
  const versionValue = versionInput ? versionInput.value.trim() : "";
  if (versionValue && source === "github") {
    body.version = versionValue;
  }

  // Set navigation warning
  window.onbeforeunload = function () {
    return "An upgrade is in progress. Leaving may cause issues.";
  };

  // Show the full-screen overlay
  const overlay = document.getElementById("upgrade-overlay");
  if (overlay) {
    overlay.style.display = "flex";
  }

  try {
    const res = await fetch(`${API_BASE}/api/system/upgrade`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const result = await res.json();

    if (result.success) {
      startResilientUpgradePolling();
    } else {
      window.onbeforeunload = null;
      if (overlay) overlay.style.display = "none";
      showToast(result.error || "Failed to start upgrade", "error");
    }
  } catch (error) {
    window.onbeforeunload = null;
    if (overlay) overlay.style.display = "none";
    showToast("Failed to start upgrade: " + error.message, "error");
  }
}

// ============================================
// Resilient Upgrade Polling & Overlay
// ============================================

function startResilientUpgradePolling() {
  const overlay = document.getElementById("upgrade-overlay");
  if (overlay) overlay.style.display = "flex";

  let apiDown = false;
  let downSince = null;

  const poll = async () => {
    try {
      if (apiDown) {
        // Recovery polling — hit health endpoint
        const healthResp = await fetch(
          `${API_BASE}/api/system/health`,
          { signal: AbortSignal.timeout(3000) },
        );
        if (healthResp.ok) {
          apiDown = false;
          const statusResp = await fetch(
            `${API_BASE}/api/system/upgrade/status`,
          );
          if (statusResp.ok) {
            const data = await statusResp.json();
            showUpgradeResult(data);
            return;
          }
        }
      } else {
        // Normal polling — hit status endpoint
        const resp = await fetch(
          `${API_BASE}/api/system/upgrade/status`,
          { signal: AbortSignal.timeout(3000) },
        );
        if (resp.ok) {
          const data = await resp.json();
          updateOverlayStages(data);
          if (!data.running && data.stage === "complete") {
            showUpgradeResult(data);
            return;
          }
        }
      }
    } catch {
      if (!apiDown) {
        apiDown = true;
        downSince = Date.now();
        const statusEl = document.getElementById("upgrade-overlay-status");
        if (statusEl) {
          statusEl.textContent = "Services restarting \u2014 waiting for API...";
        }
      }
      if (downSince && Date.now() - downSince > 120000) {
        showUpgradeTimeout();
        return;
      }
    }
    setTimeout(poll, 2000);
  };
  poll();
}

function updateOverlayStages(data) {
  const stagesEl = document.getElementById("upgrade-overlay-stages");
  if (!stagesEl) return;
  stagesEl.replaceChildren();

  const stages = [
    { key: "preflight_recheck", label: "Preflight re-validated" },
    { key: "backing_up", label: "Installation backed up" },
    { key: "stopping_services", label: "Services stopped" },
    { key: "upgrading", label: "Upgrading files" },
    { key: "starting_services", label: "Starting services" },
    { key: "verifying", label: "Verifying upgrade" },
  ];
  if (data.result && data.result.major_upgrade) {
    stages.splice(
      4,
      0,
      { key: "rebuilding_venv", label: "Rebuilding virtual environment" },
      { key: "migrating_config", label: "Migrating configuration" },
    );
  }

  const currentIdx = stages.findIndex((s) => s.key === data.stage);
  for (let i = 0; i < stages.length; i++) {
    const item = document.createElement("div");
    item.className = "upgrade-stage-item";

    const icon = document.createElement("span");
    if (i < currentIdx) {
      item.classList.add("complete");
      icon.textContent = "\u2713";
    } else if (i === currentIdx) {
      item.classList.add("active");
      icon.textContent = "\u25CF";
    } else {
      item.classList.add("pending");
      icon.textContent = "\u25CB";
    }

    const label = document.createElement("span");
    label.textContent = stages[i].label;

    item.appendChild(icon);
    item.appendChild(label);
    stagesEl.appendChild(item);
  }

  const statusEl = document.getElementById("upgrade-overlay-status");
  if (statusEl) statusEl.textContent = data.message || "";
}

function showUpgradeResult(data) {
  window.onbeforeunload = null;
  const resultEl = document.getElementById("upgrade-overlay-result");
  if (!resultEl) return;
  resultEl.style.display = "block";
  resultEl.replaceChildren();

  if (data.success) {
    const heading = document.createElement("h2");
    heading.textContent = "Upgrade Complete!";
    heading.style.color = "#4caf50";
    heading.style.fontSize = "2rem";
    resultEl.appendChild(heading);

    if (data.result) {
      const version = document.createElement("p");
      version.textContent =
        "Version: " + (data.result.new_version || "unknown");
      version.style.fontSize = "1.25rem";
      version.style.color = "var(--ink, #FFF8DC)";
      resultEl.appendChild(version);
    }

    const countdown = document.createElement("p");
    countdown.textContent = "Reloading in 5 seconds...";
    countdown.style.color = "var(--ink-faded, #e8dcc8)";
    resultEl.appendChild(countdown);

    const reloadBtn = document.createElement("button");
    reloadBtn.textContent = "Reload Now";
    reloadBtn.className = "btn btn-primary";
    reloadBtn.addEventListener("click", () => location.reload());
    resultEl.appendChild(reloadBtn);

    let seconds = 5;
    const timer = setInterval(() => {
      seconds--;
      countdown.textContent = "Reloading in " + seconds + " seconds...";
      if (seconds <= 0) {
        clearInterval(timer);
        location.reload();
      }
    }, 1000);
  } else {
    const heading = document.createElement("h2");
    heading.textContent = "Upgrade Failed";
    heading.style.color = "#f44336";
    heading.style.fontSize = "2rem";
    resultEl.appendChild(heading);

    if (data.message) {
      const msg = document.createElement("p");
      msg.textContent = data.message;
      msg.style.fontSize = "1.25rem";
      msg.style.color = "var(--ink, #FFF8DC)";
      resultEl.appendChild(msg);
    }

    const hint = document.createElement("p");
    hint.textContent = "Check server logs for details.";
    hint.style.color = "var(--ink-muted, #c4b498)";
    resultEl.appendChild(hint);

    const reloadBtn = document.createElement("button");
    reloadBtn.textContent = "Reload Application";
    reloadBtn.className = "btn btn-primary";
    reloadBtn.addEventListener("click", () => location.reload());
    resultEl.appendChild(reloadBtn);
  }
}

function showUpgradeTimeout() {
  window.onbeforeunload = null;
  const statusEl = document.getElementById("upgrade-overlay-status");
  if (statusEl) {
    statusEl.textContent =
      "Upgrade may have issues \u2014 API has not responded for 2 minutes.";
    statusEl.style.color = "#ff9800";
  }
  const resultEl = document.getElementById("upgrade-overlay-result");
  if (resultEl) {
    resultEl.style.display = "block";
    resultEl.replaceChildren();
    const btn = document.createElement("button");
    btn.textContent = "Try Reloading";
    btn.className = "btn btn-primary";
    btn.addEventListener("click", () => location.reload());
    resultEl.appendChild(btn);
  }
}
