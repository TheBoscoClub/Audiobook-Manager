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

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initDatabaseSection();
    initAudiobooksSection();
    initDuplicatesSection();
    initBulkSection();
    initModals();

    // Load initial stats
    loadDatabaseStats();
});

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
    document.getElementById('rescan-library')?.addEventListener('click', rescanLibrary);
    document.getElementById('reimport-db')?.addEventListener('click', reimportDatabase);
    document.getElementById('generate-hashes')?.addEventListener('click', generateHashes);
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

        // Update UI
        document.getElementById('db-total-books').textContent = stats.total_books?.toLocaleString() || '-';
        document.getElementById('db-total-hours').textContent =
            stats.total_hours ? `${Math.round(stats.total_hours).toLocaleString()} hrs` : '-';
        document.getElementById('db-total-size').textContent =
            stats.total_size_gb ? `${stats.total_size_gb.toFixed(1)} GB` : '-';
        document.getElementById('db-total-authors').textContent = stats.total_authors?.toLocaleString() || '-';
        document.getElementById('db-total-narrators').textContent = stats.total_narrators?.toLocaleString() || '-';
        document.getElementById('db-hash-count').textContent =
            hashStats.hashed_count ? `${hashStats.hashed_count} / ${hashStats.total_count}` : '-';
        document.getElementById('db-duplicate-groups').textContent = hashStats.duplicate_groups || '-';
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
