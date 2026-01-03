/**
 * The Back Office - Library Utilities
 * JavaScript for database management, metadata editing, duplicates, and bulk operations
 */

// Use empty string for API_BASE since fetch URLs already include /api/ prefix
// This allows the proxy server to properly route requests
const API_BASE = '';

// State
let currentSection = 'database';
let editingAudiobook = null;
let duplicatesData = [];
let bulkSelection = new Set();
let duplicateSelection = new Set();
let activeOperationId = null;
let pollingInterval = null;

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initDatabaseSection();
    initConversionSection();
    initAudiobooksSection();
    initDuplicatesSection();
    initBulkSection();
    initModals();
    initOperationStatus();

    // Load initial stats
    loadDatabaseStats();

    // Load version and initial queue status
    loadAppVersion();
    loadHeaderQueueStatus();

    // Check for any active operations on page load
    checkActiveOperations();
});

// ============================================
// Header Indicators (Version & Queue)
// ============================================

async function loadAppVersion() {
    try {
        const res = await fetch(`${API_BASE}/health`);
        const data = await res.json();
        const versionEl = document.getElementById('app-version');
        if (versionEl && data.version) {
            versionEl.textContent = data.version;
        }
    } catch (err) {
        console.warn('Could not load app version:', err);
    }
}

async function loadHeaderQueueStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/conversion/status`);
        const data = await res.json();
        if (data.success) {
            updateHeaderQueueIndicator(data.status);
        }
    } catch (err) {
        console.warn('Could not load queue status:', err);
    }
}

function updateHeaderQueueIndicator(status) {
    const queueEl = document.getElementById('queue-indicator');
    const countEl = document.getElementById('queue-count');
    if (!queueEl || !countEl) return;

    // remaining = source files not yet in library (includes queue + staged + converting)
    const remaining = status.remaining || 0;
    countEl.textContent = remaining.toLocaleString();

    // Visual feedback based on queue state
    if (remaining > 0) {
        queueEl.classList.add('has-items');
        queueEl.title = `${remaining} books remaining to convert`;
    } else {
        queueEl.classList.remove('has-items');
        queueEl.title = 'All books converted';
    }
}

// ============================================
// Tab Navigation
// ============================================

function initTabs() {
    document.querySelectorAll('.cabinet-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const section = tab.dataset.section;
            switchSection(section);
        });
    });
}

function switchSection(section) {
    // Update tabs
    document.querySelectorAll('.cabinet-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.section === section);
    });

    // Update content
    document.querySelectorAll('.drawer-content').forEach(content => {
        content.classList.toggle('active', content.id === `${section}-section`);
    });

    currentSection = section;
}

// ============================================
// Database Management
// ============================================

function initDatabaseSection() {
    document.getElementById('refresh-stats')?.addEventListener('click', loadDatabaseStats);
    document.getElementById('add-new-audiobooks')?.addEventListener('click', addNewAudiobooks);
    document.getElementById('rescan-library')?.addEventListener('click', rescanLibraryAsync);
    document.getElementById('reimport-db')?.addEventListener('click', reimportDatabaseAsync);
    document.getElementById('generate-hashes')?.addEventListener('click', generateHashesAsync);
    document.getElementById('vacuum-db')?.addEventListener('click', vacuumDatabase);
    document.getElementById('export-db')?.addEventListener('click', exportDatabase);
    document.getElementById('export-json')?.addEventListener('click', exportJson);
    document.getElementById('export-csv')?.addEventListener('click', exportCsv);
}

async function loadDatabaseStats() {
    try {
        // Fetch stats from API
        const [statsRes, hashRes] = await Promise.all([
            fetch(`${API_BASE}/api/stats`),
            fetch(`${API_BASE}/api/hash-stats`)
        ]);

        const stats = await statsRes.json();
        const hashStats = await hashRes.json();

        // Update UI - map API field names to display elements
        document.getElementById('db-total-books').textContent = stats.total_audiobooks?.toLocaleString() || '-';
        document.getElementById('db-total-hours').textContent =
            stats.total_hours ? `${Math.round(stats.total_hours).toLocaleString()} hrs` : '-';
        document.getElementById('db-total-size').textContent =
            stats.total_size_gb ? `${stats.total_size_gb.toFixed(1)} GB` : '-';
        document.getElementById('db-total-authors').textContent = stats.unique_authors?.toLocaleString() || '-';
        document.getElementById('db-total-narrators').textContent = stats.unique_narrators?.toLocaleString() || '-';
        document.getElementById('db-hash-count').textContent =
            hashStats.hashed_count !== undefined ? `${hashStats.hashed_count} / ${hashStats.total_audiobooks}` : '-';
        document.getElementById('db-duplicate-groups').textContent =
            hashStats.duplicate_groups !== undefined ? hashStats.duplicate_groups : '-';
        document.getElementById('db-file-size').textContent = stats.database_size_mb
            ? `${stats.database_size_mb.toFixed(1)} MB` : '-';

    } catch (error) {
        console.error('Failed to load stats:', error);
        showToast('Failed to load database statistics', 'error');
    }
}

async function rescanLibrary() {
    showProgress('Scanning Library', 'Scanning audiobook directory for new files...');
    try {
        const res = await fetch(`${API_BASE}/api/utilities/rescan`, { method: 'POST' });
        const result = await res.json();
        hideProgress();

        if (result.success) {
            showToast(`Scan complete: ${result.files_found} files found`, 'success');
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Scan failed', 'error');
        }
    } catch (error) {
        hideProgress();
        showToast('Failed to start scan: ' + error.message, 'error');
    }
}

async function reimportDatabase() {
    if (!await confirmAction('Reimport Database',
        'This will rebuild the database from scan results. Existing narrator and genre data will be preserved. Continue?')) {
        return;
    }

    showProgress('Reimporting Database', 'Importing audiobooks to database...');
    try {
        const res = await fetch(`${API_BASE}/api/utilities/reimport`, { method: 'POST' });
        const result = await res.json();
        hideProgress();

        if (result.success) {
            showToast(`Import complete: ${result.imported_count} audiobooks`, 'success');
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Import failed', 'error');
        }
    } catch (error) {
        hideProgress();
        showToast('Failed to reimport: ' + error.message, 'error');
    }
}

async function generateHashes() {
    showProgress('Generating Hashes', 'Calculating SHA-256 hashes for all audiobooks...');
    try {
        const res = await fetch(`${API_BASE}/api/utilities/generate-hashes`, { method: 'POST' });
        const result = await res.json();
        hideProgress();

        if (result.success) {
            showToast(`Generated ${result.hashes_generated} hashes`, 'success');
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Hash generation failed', 'error');
        }
    } catch (error) {
        hideProgress();
        showToast('Failed to generate hashes: ' + error.message, 'error');
    }
}

async function vacuumDatabase() {
    showProgress('Vacuuming Database', 'Optimizing database and reclaiming space...');
    try {
        const res = await fetch(`${API_BASE}/api/utilities/vacuum`, { method: 'POST' });
        const result = await res.json();
        hideProgress();

        if (result.success) {
            showToast(`Database vacuumed. Space reclaimed: ${result.space_reclaimed_mb?.toFixed(1) || '?'} MB`, 'success');
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Vacuum failed', 'error');
        }
    } catch (error) {
        hideProgress();
        showToast('Failed to vacuum database: ' + error.message, 'error');
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
    const searchInput = document.getElementById('edit-search');
    const searchBtn = document.getElementById('edit-search-btn');

    searchBtn?.addEventListener('click', () => searchForEdit(searchInput.value));
    searchInput?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') searchForEdit(searchInput.value);
    });

    document.getElementById('close-edit-form')?.addEventListener('click', closeEditForm);
    document.getElementById('cancel-edit')?.addEventListener('click', closeEditForm);
    document.getElementById('edit-audiobook-form')?.addEventListener('submit', saveAudiobook);
    document.getElementById('delete-audiobook')?.addEventListener('click', deleteAudiobook);
}

async function searchForEdit(query) {
    if (!query.trim()) return;

    const resultsContainer = document.getElementById('edit-search-results');
    resultsContainer.innerHTML = '<p class="placeholder-text">Searching...</p>';

    try {
        const res = await fetch(`${API_BASE}/api/audiobooks?search=${encodeURIComponent(query)}&per_page=20`);
        const data = await res.json();

        if (data.audiobooks?.length > 0) {
            resultsContainer.innerHTML = data.audiobooks.map(book => `
                <div class="search-result-item" data-id="${book.id}">
                    <img src="${book.cover_url || '/api/covers/default.jpg'}"
                         alt="" class="result-cover"
                         onerror="this.src='/api/covers/default.jpg'">
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(book.title)}</div>
                        <div class="result-meta">${escapeHtml(book.author)} | ${escapeHtml(book.narrator || 'Unknown narrator')}</div>
                    </div>
                </div>
            `).join('');

            // Add click handlers
            resultsContainer.querySelectorAll('.search-result-item').forEach(item => {
                item.addEventListener('click', () => loadAudiobookForEdit(item.dataset.id));
            });
        } else {
            resultsContainer.innerHTML = '<p class="placeholder-text">No audiobooks found</p>';
        }
    } catch (error) {
        resultsContainer.innerHTML = '<p class="placeholder-text">Search failed</p>';
        showToast('Search failed: ' + error.message, 'error');
    }
}

async function loadAudiobookForEdit(id) {
    try {
        const res = await fetch(`${API_BASE}/api/audiobooks/${id}`);
        const book = await res.json();

        editingAudiobook = book;

        // Populate form
        document.getElementById('edit-id').value = book.id;
        document.getElementById('edit-title').value = book.title || '';
        document.getElementById('edit-author').value = book.author || '';
        document.getElementById('edit-narrator').value = book.narrator || '';
        document.getElementById('edit-series').value = book.series || '';
        document.getElementById('edit-series-seq').value = book.series_sequence || '';
        document.getElementById('edit-publisher').value = book.publisher || '';
        document.getElementById('edit-year').value = book.published_year || '';
        document.getElementById('edit-asin').value = book.asin || '';
        document.getElementById('edit-file-path').textContent = book.file_path || '-';

        // Show form
        document.getElementById('edit-form-container').style.display = 'block';
        document.getElementById('edit-form-container').scrollIntoView({ behavior: 'smooth' });

    } catch (error) {
        showToast('Failed to load audiobook: ' + error.message, 'error');
    }
}

function closeEditForm() {
    document.getElementById('edit-form-container').style.display = 'none';
    editingAudiobook = null;
}

async function saveAudiobook(e) {
    e.preventDefault();

    const id = document.getElementById('edit-id').value;
    const data = {
        title: document.getElementById('edit-title').value,
        author: document.getElementById('edit-author').value,
        narrator: document.getElementById('edit-narrator').value,
        series: document.getElementById('edit-series').value || null,
        series_sequence: document.getElementById('edit-series-seq').value || null,
        publisher: document.getElementById('edit-publisher').value || null,
        published_year: document.getElementById('edit-year').value || null,
        asin: document.getElementById('edit-asin').value || null
    };

    try {
        const res = await fetch(`${API_BASE}/api/audiobooks/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await res.json();

        if (result.success) {
            showToast('Audiobook updated successfully', 'success');
            closeEditForm();
            // Refresh search results
            const query = document.getElementById('edit-search').value;
            if (query) searchForEdit(query);
        } else {
            showToast(result.error || 'Update failed', 'error');
        }
    } catch (error) {
        showToast('Failed to save: ' + error.message, 'error');
    }
}

async function deleteAudiobook() {
    if (!editingAudiobook) return;

    const confirmed = await confirmAction(
        'Delete Audiobook',
        `Are you sure you want to delete "${editingAudiobook.title}"?\n\nThis will remove it from the database. The audio file will NOT be deleted.`
    );

    if (!confirmed) return;

    try {
        const res = await fetch(`${API_BASE}/api/audiobooks/${editingAudiobook.id}`, {
            method: 'DELETE'
        });

        const result = await res.json();

        if (result.success) {
            showToast('Audiobook deleted from database', 'success');
            closeEditForm();
            document.getElementById('edit-search-results').innerHTML =
                '<p class="placeholder-text">Enter a search term to find audiobooks</p>';
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Delete failed', 'error');
        }
    } catch (error) {
        showToast('Failed to delete: ' + error.message, 'error');
    }
}

// ============================================
// Duplicates Section
// ============================================

function initDuplicatesSection() {
    document.getElementById('find-duplicates')?.addEventListener('click', findDuplicates);
    document.getElementById('select-all-dups')?.addEventListener('click', selectAllDuplicates);
    document.getElementById('deselect-all-dups')?.addEventListener('click', deselectAllDuplicates);
    document.getElementById('delete-selected-dups')?.addEventListener('click', deleteSelectedDuplicates);
}

async function findDuplicates() {
    const method = document.querySelector('input[name="dup-method"]:checked').value;
    const endpoint = method === 'hash' ? '/api/duplicates/by-hash' : '/api/duplicates/by-title';

    const listContainer = document.getElementById('duplicates-list');
    listContainer.innerHTML = '<p class="placeholder-text">Searching for duplicates...</p>';

    try {
        const res = await fetch(`${API_BASE}${endpoint}`);
        const data = await res.json();

        duplicatesData = data.duplicate_groups || [];
        duplicateSelection.clear();

        document.getElementById('dup-group-count').textContent = duplicatesData.length;

        if (duplicatesData.length > 0) {
            renderDuplicates();
            document.getElementById('dup-actions').style.display = 'flex';
        } else {
            listContainer.innerHTML = '<p class="placeholder-text">No duplicates found</p>';
            document.getElementById('dup-actions').style.display = 'none';
        }

    } catch (error) {
        listContainer.innerHTML = '<p class="placeholder-text">Failed to find duplicates</p>';
        showToast('Failed to find duplicates: ' + error.message, 'error');
    }
}

function renderDuplicates() {
    const listContainer = document.getElementById('duplicates-list');

    listContainer.innerHTML = duplicatesData.map((group, groupIdx) => `
        <div class="duplicate-group">
            <div class="duplicate-group-header">
                ${escapeHtml(group.title || group.hash?.substring(0, 16) + '...')}
                (${group.items.length} copies)
            </div>
            ${group.items.map((item, itemIdx) => `
                <div class="duplicate-item ${itemIdx === 0 ? 'keep' : ''}">
                    ${itemIdx === 0
                        ? '<span class="keep-badge">KEEP</span>'
                        : `<input type="checkbox" class="duplicate-checkbox"
                                  data-group="${groupIdx}" data-item="${itemIdx}"
                                  data-id="${item.id}">`
                    }
                    <div class="result-info">
                        <div class="result-title">${escapeHtml(item.title)}</div>
                        <div class="result-meta">
                            ${escapeHtml(item.author)} |
                            ${item.file_size_mb?.toFixed(1) || '?'} MB |
                            ${item.file_path ? item.file_path.split('/').pop() : 'Unknown file'}
                        </div>
                    </div>
                </div>
            `).join('')}
        </div>
    `).join('');

    // Add change handlers to checkboxes
    listContainer.querySelectorAll('.duplicate-checkbox').forEach(cb => {
        cb.addEventListener('change', updateDuplicateSelection);
    });
}

function updateDuplicateSelection() {
    duplicateSelection.clear();
    document.querySelectorAll('.duplicate-checkbox:checked').forEach(cb => {
        duplicateSelection.add(parseInt(cb.dataset.id));
    });

    document.getElementById('selected-count').textContent = duplicateSelection.size;
    document.getElementById('delete-selected-dups').disabled = duplicateSelection.size === 0;
}

function selectAllDuplicates() {
    document.querySelectorAll('.duplicate-checkbox').forEach(cb => {
        cb.checked = true;
    });
    updateDuplicateSelection();
}

function deselectAllDuplicates() {
    document.querySelectorAll('.duplicate-checkbox').forEach(cb => {
        cb.checked = false;
    });
    updateDuplicateSelection();
}

async function deleteSelectedDuplicates() {
    if (duplicateSelection.size === 0) return;

    const confirmed = await confirmAction(
        'Delete Duplicates',
        `Are you sure you want to delete ${duplicateSelection.size} duplicate audiobook(s)?\n\nThis will remove them from the database AND delete the audio files.`
    );

    if (!confirmed) return;

    showProgress('Deleting Duplicates', `Removing ${duplicateSelection.size} files...`);

    try {
        const res = await fetch(`${API_BASE}/api/audiobooks/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(duplicateSelection),
                delete_files: true
            })
        });

        const result = await res.json();
        hideProgress();

        if (result.success) {
            showToast(`Deleted ${result.deleted_count} audiobooks`, 'success');
            findDuplicates(); // Refresh
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Delete failed', 'error');
        }
    } catch (error) {
        hideProgress();
        showToast('Failed to delete: ' + error.message, 'error');
    }
}

// ============================================
// Bulk Operations
// ============================================

function initBulkSection() {
    const filterType = document.getElementById('bulk-filter-type');
    const filterValueGroup = document.getElementById('bulk-filter-value-group');

    filterType?.addEventListener('change', () => {
        const needsValue = ['author', 'narrator', 'series'].includes(filterType.value);
        filterValueGroup.style.display = needsValue ? 'flex' : 'none';
    });

    document.getElementById('bulk-load')?.addEventListener('click', loadBulkAudiobooks);
    document.getElementById('bulk-select-all')?.addEventListener('change', toggleBulkSelectAll);
    document.getElementById('bulk-update-btn')?.addEventListener('click', bulkUpdateField);
    document.getElementById('bulk-delete-btn')?.addEventListener('click', bulkDelete);
}

async function loadBulkAudiobooks() {
    const filterType = document.getElementById('bulk-filter-type').value;
    const filterValue = document.getElementById('bulk-filter-value').value;

    let endpoint = `${API_BASE}/api/audiobooks?per_page=200`;

    if (filterType === 'author' && filterValue) {
        endpoint += `&author=${encodeURIComponent(filterValue)}`;
    } else if (filterType === 'narrator' && filterValue) {
        endpoint += `&narrator=${encodeURIComponent(filterValue)}`;
    } else if (filterType === 'series' && filterValue) {
        endpoint += `&series=${encodeURIComponent(filterValue)}`;
    } else if (filterType === 'no-narrator') {
        endpoint = `${API_BASE}/api/audiobooks/missing-narrator`;
    } else if (filterType === 'no-hash') {
        endpoint = `${API_BASE}/api/audiobooks/missing-hash`;
    }

    const listContainer = document.getElementById('bulk-list');
    listContainer.innerHTML = '<p class="placeholder-text">Loading...</p>';

    try {
        const res = await fetch(endpoint);
        const data = await res.json();

        const audiobooks = data.audiobooks || data || [];
        bulkSelection.clear();

        if (audiobooks.length > 0) {
            renderBulkList(audiobooks);
            document.getElementById('bulk-selection-bar').style.display = 'flex';
            document.getElementById('bulk-actions-card').style.display = 'block';
        } else {
            listContainer.innerHTML = '<p class="placeholder-text">No audiobooks found</p>';
            document.getElementById('bulk-selection-bar').style.display = 'none';
            document.getElementById('bulk-actions-card').style.display = 'none';
        }

    } catch (error) {
        listContainer.innerHTML = '<p class="placeholder-text">Failed to load audiobooks</p>';
        showToast('Failed to load: ' + error.message, 'error');
    }
}

function renderBulkList(audiobooks) {
    const listContainer = document.getElementById('bulk-list');

    listContainer.innerHTML = audiobooks.map(book => `
        <div class="bulk-item">
            <input type="checkbox" class="bulk-checkbox" data-id="${book.id}">
            <div class="result-info">
                <div class="result-title">${escapeHtml(book.title)}</div>
                <div class="result-meta">
                    ${escapeHtml(book.author)} |
                    ${escapeHtml(book.narrator || 'No narrator')} |
                    ${book.series ? escapeHtml(book.series) : 'No series'}
                </div>
            </div>
        </div>
    `).join('');

    // Add change handlers
    listContainer.querySelectorAll('.bulk-checkbox').forEach(cb => {
        cb.addEventListener('change', updateBulkSelection);
    });
}

function updateBulkSelection() {
    bulkSelection.clear();
    document.querySelectorAll('.bulk-checkbox:checked').forEach(cb => {
        bulkSelection.add(parseInt(cb.dataset.id));
    });

    document.getElementById('bulk-selected-count').textContent = bulkSelection.size;
    document.getElementById('bulk-select-all').checked =
        bulkSelection.size === document.querySelectorAll('.bulk-checkbox').length;
}

function toggleBulkSelectAll() {
    const selectAll = document.getElementById('bulk-select-all').checked;
    document.querySelectorAll('.bulk-checkbox').forEach(cb => {
        cb.checked = selectAll;
    });
    updateBulkSelection();
}

async function bulkUpdateField() {
    if (bulkSelection.size === 0) {
        showToast('No audiobooks selected', 'error');
        return;
    }

    const field = document.getElementById('bulk-update-field').value;
    const value = document.getElementById('bulk-update-value').value;

    if (!field) {
        showToast('Please select a field to update', 'error');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/audiobooks/bulk-update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(bulkSelection),
                field: field,
                value: value
            })
        });

        const result = await res.json();

        if (result.success) {
            showToast(`Updated ${result.updated_count} audiobooks`, 'success');
            loadBulkAudiobooks(); // Refresh
        } else {
            showToast(result.error || 'Update failed', 'error');
        }
    } catch (error) {
        showToast('Failed to update: ' + error.message, 'error');
    }
}

async function bulkDelete() {
    if (bulkSelection.size === 0) {
        showToast('No audiobooks selected', 'error');
        return;
    }

    const confirmed = await confirmAction(
        'Delete Audiobooks',
        `Are you sure you want to delete ${bulkSelection.size} audiobook(s)?\n\nThis will remove them from the database. The audio files will NOT be deleted.`
    );

    if (!confirmed) return;

    try {
        const res = await fetch(`${API_BASE}/api/audiobooks/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(bulkSelection),
                delete_files: false
            })
        });

        const result = await res.json();

        if (result.success) {
            showToast(`Deleted ${result.deleted_count} audiobooks from database`, 'success');
            loadBulkAudiobooks(); // Refresh
            loadDatabaseStats();
        } else {
            showToast(result.error || 'Delete failed', 'error');
        }
    } catch (error) {
        showToast('Failed to delete: ' + error.message, 'error');
    }
}

// ============================================
// Modals & Toasts
// ============================================

function initModals() {
    document.getElementById('modal-close')?.addEventListener('click', hideConfirmModal);
    document.getElementById('confirm-cancel')?.addEventListener('click', hideConfirmModal);
}

let confirmResolve = null;

function confirmAction(title, message) {
    return new Promise((resolve) => {
        confirmResolve = resolve;

        document.getElementById('confirm-title').textContent = title;
        document.getElementById('confirm-body').textContent = message;
        document.getElementById('confirm-modal').classList.add('active');

        const confirmBtn = document.getElementById('confirm-action');
        confirmBtn.onclick = () => {
            hideConfirmModal();
            resolve(true);
        };
    });
}

function hideConfirmModal() {
    document.getElementById('confirm-modal').classList.remove('active');
    if (confirmResolve) {
        confirmResolve(false);
        confirmResolve = null;
    }
}

function showProgress(title, message) {
    document.getElementById('progress-title').textContent = title;
    document.getElementById('progress-message').textContent = message;
    document.getElementById('progress-output').textContent = '';
    document.getElementById('progress-modal').classList.add('active');
}

function hideProgress() {
    document.getElementById('progress-modal').classList.remove('active');
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ============================================
// Utilities
// ============================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// Operation Status Tracking & Polling
// ============================================

function initOperationStatus() {
    // Cancel button in status banner
    document.getElementById('status-cancel-btn')?.addEventListener('click', cancelActiveOperation);

    // Close button in progress modal
    document.getElementById('modal-close-progress')?.addEventListener('click', hideProgressModal);
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
        console.error('Failed to check active operations:', error);
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
        const res = await fetch(`${API_BASE}/api/operations/status/${activeOperationId}`);
        const status = await res.json();

        updateStatusDisplay(status);

        // Check if operation completed
        if (status.state !== 'running' && status.state !== 'pending') {
            stopOperationPolling();
            handleOperationComplete(status);
        }
    } catch (error) {
        console.error('Polling error:', error);
    }
}

function updateStatusDisplay(status) {
    // Update banner
    document.getElementById('status-operation-name').textContent =
        status.description || `${status.type} operation`;
    document.getElementById('status-progress-fill').style.width = `${status.progress}%`;
    document.getElementById('status-progress-percent').textContent = `${status.progress}%`;
    document.getElementById('status-message').textContent = status.message || 'Processing...';

    // Update modal if visible
    const modal = document.getElementById('progress-modal');
    if (modal.classList.contains('active')) {
        document.getElementById('modal-progress-fill').style.width = `${status.progress}%`;
        document.getElementById('modal-progress-percent').textContent = `${status.progress}%`;
        document.getElementById('progress-message').textContent = status.message || 'Processing...';

        if (status.elapsed_seconds) {
            const elapsed = Math.round(status.elapsed_seconds);
            document.getElementById('modal-progress-elapsed').textContent =
                `Elapsed: ${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
        }

        document.getElementById('modal-operation-id').textContent = `ID: ${status.id}`;
    }
}

function handleOperationComplete(status) {
    activeOperationId = null;
    hideStatusBanner();

    // Show modal close button
    document.getElementById('modal-close-progress')?.style.setProperty('display', 'inline-block');

    // Update modal with final status
    const modal = document.getElementById('progress-modal');
    if (modal.classList.contains('active')) {
        document.getElementById('progress-spinner')?.classList.add('hidden');

        if (status.state === 'completed') {
            document.getElementById('progress-message').textContent = 'Operation completed successfully!';
            document.getElementById('modal-progress-fill').style.backgroundColor = '#2e7d32';

            // Show result details
            if (status.result) {
                const output = document.getElementById('progress-output');
                let resultText = '';
                if (status.result.added !== undefined) {
                    resultText = `Added: ${status.result.added} audiobooks`;
                    if (status.result.skipped) resultText += ` | Skipped: ${status.result.skipped}`;
                    if (status.result.errors) resultText += ` | Errors: ${status.result.errors}`;
                } else if (status.result.files_found !== undefined) {
                    resultText = `Files found: ${status.result.files_found}`;
                } else if (status.result.imported_count !== undefined) {
                    resultText = `Imported: ${status.result.imported_count} audiobooks`;
                } else if (status.result.hashes_generated !== undefined) {
                    resultText = `Hashes generated: ${status.result.hashes_generated}`;
                }
                output.textContent = resultText;
            }

            showToast('Operation completed successfully', 'success');
        } else if (status.state === 'failed') {
            document.getElementById('progress-message').textContent = 'Operation failed';
            document.getElementById('modal-progress-fill').style.backgroundColor = '#c62828';
            document.getElementById('progress-output').textContent = status.error || 'Unknown error';
            showToast(`Operation failed: ${status.error}`, 'error');
        } else if (status.state === 'cancelled') {
            document.getElementById('progress-message').textContent = 'Operation cancelled';
            showToast('Operation cancelled', 'info');
        }
    } else {
        // Modal not visible, just show toast
        if (status.state === 'completed') {
            showToast('Background operation completed', 'success');
        } else if (status.state === 'failed') {
            showToast(`Background operation failed: ${status.error}`, 'error');
        }
    }

    // Refresh stats
    loadDatabaseStats();
}

function showStatusBanner(status) {
    const banner = document.getElementById('operation-status-banner');
    banner.style.display = 'block';
    updateStatusDisplay(status);
}

function hideStatusBanner() {
    document.getElementById('operation-status-banner').style.display = 'none';
}

function hideProgressModal() {
    document.getElementById('progress-modal').classList.remove('active');
    // Reset modal state
    document.getElementById('modal-progress-fill').style.width = '0%';
    document.getElementById('modal-progress-fill').style.backgroundColor = '';
    document.getElementById('modal-close-progress').style.display = 'none';
    document.getElementById('progress-output').textContent = '';
    document.getElementById('modal-progress-elapsed').textContent = '';
}

async function cancelActiveOperation() {
    if (!activeOperationId) return;

    try {
        await fetch(`${API_BASE}/api/operations/cancel/${activeOperationId}`, { method: 'POST' });
        showToast('Cancellation requested', 'info');
    } catch (error) {
        showToast('Failed to cancel operation', 'error');
    }
}

// ============================================
// Async Operations with Progress Tracking
// ============================================

async function addNewAudiobooks() {
    // Check if already running
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    showProgressModal('Adding New Audiobooks', 'Scanning for new files...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/add-new`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ calculate_hashes: true })
        });

        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Adding new audiobooks', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            if (result.operation_id) {
                // Already running
                activeOperationId = result.operation_id;
                showStatusBanner({ id: result.operation_id, description: 'Adding new audiobooks', progress: 0 });
                startOperationPolling();
                showToast('Operation already in progress', 'info');
            } else {
                showToast(result.error || 'Failed to start operation', 'error');
            }
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start add operation: ' + error.message, 'error');
    }
}

async function rescanLibraryAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    if (!await confirmAction('Full Library Rescan',
        'This will scan ALL files in the library, which can take a long time for large libraries.\n\nFor adding new books only, use "Add New" instead.\n\nContinue with full rescan?')) {
        return;
    }

    showProgressModal('Scanning Library', 'Starting full library scan...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/rescan-async`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Full library scan', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start scan', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start scan: ' + error.message, 'error');
    }
}

async function reimportDatabaseAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    if (!await confirmAction('Reimport Database',
        'This will rebuild the database from scan results. Existing narrator and genre data will be preserved. Continue?')) {
        return;
    }

    showProgressModal('Reimporting Database', 'Starting database import...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/reimport-async`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Database import', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start import', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start import: ' + error.message, 'error');
    }
}

async function generateHashesAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    showProgressModal('Generating Hashes', 'Calculating SHA-256 hashes...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/generate-hashes-async`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Hash generation', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start hash generation', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start hash generation: ' + error.message, 'error');
    }
}

function showProgressModal(title, message) {
    document.getElementById('progress-title').textContent = title;
    document.getElementById('progress-message').textContent = message;
    document.getElementById('progress-output').textContent = '';
    document.getElementById('modal-progress-fill').style.width = '0%';
    document.getElementById('modal-progress-fill').style.backgroundColor = '';
    document.getElementById('modal-progress-percent').textContent = '0%';
    document.getElementById('modal-progress-elapsed').textContent = '';
    document.getElementById('modal-close-progress').style.display = 'none';
    document.getElementById('progress-modal').classList.add('active');
}

// ============================================
// Conversion Monitor Section
// ============================================

let conversionRefreshInterval = null;
let conversionRateTracker = {
    prevCount: null,  // null = not yet initialized
    prevTime: Date.now(),
    rate: 0,
    stableTime: 0,  // track how long count has been unchanged
    prevReadBytes: 0,
    prevWriteBytes: 0,
    readThroughput: 0,  // bytes per second
    writeThroughput: 0
};

// Per-job throughput tracking
let jobThroughputTracker = {};  // pid -> { prevReadBytes, throughput }
let conversionSortBy = 'percent';  // 'percent', 'throughput', 'name'

function initConversionSection() {
    // Refresh button
    document.getElementById('conv-refresh')?.addEventListener('click', loadConversionStatus);

    // Auto-refresh toggle
    document.getElementById('conv-auto-refresh')?.addEventListener('change', (e) => {
        if (e.target.checked) {
            startConversionAutoRefresh();
        } else {
            stopConversionAutoRefresh();
        }
    });

    // Refresh interval change
    document.getElementById('conv-refresh-interval')?.addEventListener('change', () => {
        if (document.getElementById('conv-auto-refresh')?.checked) {
            stopConversionAutoRefresh();
            startConversionAutoRefresh();
        }
    });

    // Start auto-refresh if checkbox is checked
    if (document.getElementById('conv-auto-refresh')?.checked) {
        startConversionAutoRefresh();
    }

    // Expandable details panel toggle
    const rateToggle = document.getElementById('conv-rate-toggle');
    const detailsPanel = document.getElementById('conv-details-panel');
    if (rateToggle && detailsPanel) {
        rateToggle.addEventListener('click', () => {
            rateToggle.classList.toggle('expanded');
            detailsPanel.classList.toggle('expanded');
        });
    }

    // Sort button handlers
    document.querySelectorAll('.sort-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const sortBy = btn.dataset.sort;
            if (sortBy) {
                conversionSortBy = sortBy;
                // Update active state
                document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                // Refresh to apply sort
                loadConversionStatus();
            }
        });
    });
}

function startConversionAutoRefresh() {
    stopConversionAutoRefresh();
    const intervalSec = parseInt(document.getElementById('conv-refresh-interval')?.value || '10');
    loadConversionStatus();
    conversionRefreshInterval = setInterval(loadConversionStatus, intervalSec * 1000);
}

function stopConversionAutoRefresh() {
    if (conversionRefreshInterval) {
        clearInterval(conversionRefreshInterval);
        conversionRefreshInterval = null;
    }
}

async function loadConversionStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/conversion/status`);
        const data = await res.json();

        if (!data.success) {
            console.error('Failed to load conversion status:', data.error);
            return;
        }

        const status = data.status;
        const processes = data.processes;
        const system = data.system;

        // Update progress bar
        const progressFill = document.getElementById('conv-progress-fill');
        const percentDisplay = document.getElementById('conv-percent');
        if (progressFill && percentDisplay) {
            progressFill.style.width = `${status.percent_complete}%`;
            percentDisplay.textContent = `${status.percent_complete}%`;

            // Add complete class if done
            const container = progressFill.closest('.conversion-progress-container');
            if (container) {
                container.classList.toggle('conversion-complete', status.is_complete);
            }
        }

        // Calculate rate and ETA
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
            // Decay rate toward 0 if idle for a while
            if (conversionRateTracker.stableTime > 30) {
                conversionRateTracker.rate = 0;
            }
        }

        // Display rate
        const rateDisplay = document.getElementById('conv-rate');
        const isActivelyConverting = processes.ffmpeg_count > 0;
        if (rateDisplay) {
            if (status.is_complete) {
                rateDisplay.textContent = 'complete';
            } else if (conversionRateTracker.rate > 0) {
                rateDisplay.textContent = `${conversionRateTracker.rate.toFixed(1)} books/min`;
            } else if (isActivelyConverting) {
                // FFmpeg processes running - show count as indicator
                rateDisplay.textContent = `${processes.ffmpeg_count} active`;
            } else if (conversionRateTracker.stableTime > 10) {
                // No active processes and no completions for a while
                rateDisplay.textContent = 'idle';
            } else {
                rateDisplay.textContent = 'measuring...';
            }
        }

        // Calculate and display ETA
        const etaDisplay = document.getElementById('conv-eta');
        if (etaDisplay) {
            if (status.is_complete) {
                etaDisplay.textContent = 'Complete!';
            } else if (conversionRateTracker.rate > 0 && status.remaining > 0) {
                const etaMins = status.remaining / conversionRateTracker.rate;
                if (etaMins < 1) {
                    etaDisplay.textContent = `ETA: ${Math.round(etaMins * 60)}s`;
                } else if (etaMins < 60) {
                    etaDisplay.textContent = `ETA: ${Math.round(etaMins)}m`;
                } else {
                    const hours = Math.floor(etaMins / 60);
                    const mins = Math.round(etaMins % 60);
                    etaDisplay.textContent = `ETA: ${hours}h ${mins}m`;
                }
            } else {
                etaDisplay.textContent = 'Calculating...';
            }
        }

        // Update file counts
        document.getElementById('conv-source-count').textContent = status.source_count.toLocaleString();
        document.getElementById('conv-library-count').textContent = status.library_count.toLocaleString();
        document.getElementById('conv-staged-count').textContent = status.staged_count.toLocaleString();
        document.getElementById('conv-remaining-count').textContent = status.remaining.toLocaleString();
        document.getElementById('conv-queue-count').textContent = status.queue_count.toLocaleString();

        // Update header queue indicator (for real-time visibility)
        updateHeaderQueueIndicator(status);

        // Update system stats
        document.getElementById('conv-ffmpeg-count').textContent = processes.ffmpeg_count || '0';
        document.getElementById('conv-ffmpeg-nice').textContent = processes.ffmpeg_nice || '-';
        document.getElementById('conv-load-avg').textContent = system.load_avg || '-';
        document.getElementById('conv-tmpfs-usage').textContent = system.tmpfs_usage || '-';
        document.getElementById('conv-tmpfs-avail').textContent = system.tmpfs_avail || '-';

        // Update active badge
        const activeBadge = document.getElementById('conv-active-count');
        if (activeBadge) {
            activeBadge.textContent = `${processes.ffmpeg_count} active`;
        }

        // Update active conversions list using safe DOM methods with per-job stats
        const activeList = document.getElementById('conv-active-list');
        if (activeList) {
            // Clear existing content safely
            while (activeList.firstChild) {
                activeList.removeChild(activeList.firstChild);
            }

            // Use conversion_jobs for detailed info, fallback to active_conversions
            let jobs = processes.conversion_jobs || [];
            if (jobs.length > 0) {
                // Calculate per-job throughput
                const newTracker = {};
                jobs.forEach(job => {
                    const pid = job.pid;
                    const currentReadBytes = job.read_bytes || 0;

                    if (jobThroughputTracker[pid] && elapsed > 0) {
                        const delta = currentReadBytes - jobThroughputTracker[pid].prevReadBytes;
                        if (delta >= 0) {
                            job.throughput = delta / elapsed;  // bytes per second
                        } else {
                            job.throughput = 0;
                        }
                    } else {
                        job.throughput = 0;
                    }

                    newTracker[pid] = { prevReadBytes: currentReadBytes };
                });
                jobThroughputTracker = newTracker;

                // Sort jobs based on selected criteria
                jobs = [...jobs].sort((a, b) => {
                    switch (conversionSortBy) {
                        case 'percent':
                            return (b.percent || 0) - (a.percent || 0);  // Highest first
                        case 'throughput':
                            return (b.throughput || 0) - (a.throughput || 0);  // Highest first
                        case 'name':
                            return (a.filename || '').localeCompare(b.filename || '');
                        default:
                            return 0;
                    }
                });

                jobs.forEach(job => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'active-conversion-item';

                    // Filename
                    const filenameSpan = document.createElement('span');
                    filenameSpan.className = 'filename';
                    filenameSpan.textContent = job.display_name || job.filename || 'unknown';
                    itemDiv.appendChild(filenameSpan);

                    // Stats row
                    const statsDiv = document.createElement('div');
                    statsDiv.className = 'job-stats';

                    // Percent complete
                    const percentSpan = document.createElement('span');
                    percentSpan.className = 'job-percent';
                    percentSpan.textContent = `${job.percent || 0}%`;
                    statsDiv.appendChild(percentSpan);

                    // Throughput
                    const throughputSpan = document.createElement('span');
                    throughputSpan.className = 'job-throughput';
                    const throughputMiB = (job.throughput || 0) / 1048576;
                    throughputSpan.textContent = throughputMiB > 0.1 ? `${throughputMiB.toFixed(1)} MiB/s` : '—';
                    statsDiv.appendChild(throughputSpan);

                    // Read progress (MiB)
                    const readSpan = document.createElement('span');
                    readSpan.className = 'job-read';
                    const readMiB = (job.read_bytes || 0) / 1048576;
                    const sourceMiB = (job.source_size || 0) / 1048576;
                    readSpan.textContent = `${readMiB.toFixed(0)}/${sourceMiB.toFixed(0)} MiB`;
                    statsDiv.appendChild(readSpan);

                    itemDiv.appendChild(statsDiv);
                    activeList.appendChild(itemDiv);
                });
            } else if (processes.active_conversions && processes.active_conversions.length > 0) {
                // Fallback to legacy format
                processes.active_conversions.forEach(filename => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'active-conversion-item';
                    const filenameSpan = document.createElement('span');
                    filenameSpan.className = 'filename';
                    filenameSpan.textContent = filename;
                    itemDiv.appendChild(filenameSpan);
                    activeList.appendChild(itemDiv);
                });
            } else {
                const placeholder = document.createElement('p');
                placeholder.className = 'placeholder-text';
                placeholder.textContent = 'No active conversions';
                activeList.appendChild(placeholder);
            }
        }

        // Calculate I/O throughput (using 'elapsed' calculated before prevTime was updated)
        const currentReadBytes = processes.io_read_bytes || 0;
        const currentWriteBytes = processes.io_write_bytes || 0;

        if (conversionRateTracker.prevReadBytes > 0 && elapsed > 0) {
            const readDelta = currentReadBytes - conversionRateTracker.prevReadBytes;
            const writeDelta = currentWriteBytes - conversionRateTracker.prevWriteBytes;
            // Only update if positive (handles process restart)
            if (readDelta >= 0) {
                conversionRateTracker.readThroughput = readDelta / elapsed;
            }
            if (writeDelta >= 0) {
                conversionRateTracker.writeThroughput = writeDelta / elapsed;
            }
        }
        conversionRateTracker.prevReadBytes = currentReadBytes;
        conversionRateTracker.prevWriteBytes = currentWriteBytes;

        // Helper function to format bytes
        const formatBytes = (bytes, decimals = 1) => {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
        };

        // Update detailed stats panel
        const readThroughputEl = document.getElementById('conv-read-throughput');
        const writeThroughputEl = document.getElementById('conv-write-throughput');
        const totalReadEl = document.getElementById('conv-total-read');
        const totalWriteEl = document.getElementById('conv-total-write');

        if (readThroughputEl) {
            readThroughputEl.textContent = processes.ffmpeg_count > 0
                ? `${formatBytes(conversionRateTracker.readThroughput)}/s`
                : 'idle';
        }
        if (writeThroughputEl) {
            writeThroughputEl.textContent = processes.ffmpeg_count > 0
                ? `${formatBytes(conversionRateTracker.writeThroughput)}/s`
                : 'idle';
        }
        if (totalReadEl) {
            totalReadEl.textContent = formatBytes(currentReadBytes);
        }
        if (totalWriteEl) {
            totalWriteEl.textContent = formatBytes(currentWriteBytes);
        }

        // Update queue breakdown
        // queue_count now equals remaining (actual files left to convert)
        // waitingInQueue = files waiting to start (remaining minus active)
        // notYetQueued = 0 since all remaining files are considered "in queue"
        const waitingInQueue = Math.max(0, status.queue_count - processes.ffmpeg_count);
        const notYetQueued = 0;  // All remaining files are in the effective queue

        const activeDetailEl = document.getElementById('conv-active-detail');
        const queuedDetailEl = document.getElementById('conv-queued-detail');
        const stagingDetailEl = document.getElementById('conv-staging-detail');
        const unqueuedDetailEl = document.getElementById('conv-unqueued-detail');

        if (activeDetailEl) activeDetailEl.textContent = processes.ffmpeg_count;
        if (queuedDetailEl) queuedDetailEl.textContent = waitingInQueue;
        if (stagingDetailEl) stagingDetailEl.textContent = status.staged_count;
        if (unqueuedDetailEl) unqueuedDetailEl.textContent = notYetQueued;

        // Update active files list in details panel
        const activeFilesEl = document.getElementById('conv-active-files');
        if (activeFilesEl) {
            while (activeFilesEl.firstChild) {
                activeFilesEl.removeChild(activeFilesEl.firstChild);
            }

            if (processes.active_conversions && processes.active_conversions.length > 0) {
                processes.active_conversions.forEach(filename => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'active-file-item';

                    const filenameSpan = document.createElement('span');
                    filenameSpan.className = 'filename';
                    filenameSpan.textContent = filename;

                    itemDiv.appendChild(filenameSpan);
                    activeFilesEl.appendChild(itemDiv);
                });
            } else {
                const noFiles = document.createElement('span');
                noFiles.className = 'no-files';
                noFiles.textContent = 'No active conversions';
                activeFilesEl.appendChild(noFiles);
            }
        }

        // Update last updated timestamp
        const lastUpdated = document.getElementById('conv-last-updated');
        if (lastUpdated) {
            lastUpdated.textContent = `Updated: ${new Date().toLocaleTimeString()}`;
        }

    } catch (error) {
        console.error('Failed to load conversion status:', error);
    }
}
