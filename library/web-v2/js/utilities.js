/**
 * The Back Office - Library Utilities
 * JavaScript for database management, metadata editing, duplicates, and bulk operations
 */

// Use empty string for API_BASE since fetch URLs already include /api/ prefix
// This allows the proxy server to properly route requests
const API_BASE = '';

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
    initAudibleSection();
    initBulkSection();
    initSystemSection();
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
    document.getElementById('generate-checksums')?.addEventListener('click', generateChecksumsAsync);
    document.getElementById('vacuum-db')?.addEventListener('click', vacuumDatabase);
    document.getElementById('export-db')?.addEventListener('click', exportDatabase);
    document.getElementById('export-json')?.addEventListener('click', exportJson);
    document.getElementById('export-csv')?.addEventListener('click', exportCsv);
}

async function loadDatabaseStats() {
    try {
        // Fetch stats from API using safeFetch for proper error handling
        const [stats, hashStats] = await Promise.all([
            safeFetch(`${API_BASE}/api/stats`),
            safeFetch(`${API_BASE}/api/hash-stats`)
        ]);

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
        const result = await safeFetch(`${API_BASE}/api/utilities/rescan`, { method: 'POST' });
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

// Track current duplicate detection mode
let currentDupMode = 'title';
// Track selected paths for checksum-based deletions
let checksumPathSelection = new Set();

async function findDuplicates() {
    const method = document.querySelector('input[name="dup-method"]:checked').value;
    currentDupMode = method;

    let endpoint;
    if (method === 'hash') {
        endpoint = '/api/duplicates';  // Main endpoint is hash-based
    } else if (method === 'source-checksum') {
        endpoint = '/api/duplicates/by-checksum?type=sources';
    } else if (method === 'library-checksum') {
        endpoint = '/api/duplicates/by-checksum?type=library';
    } else {
        endpoint = '/api/duplicates/by-title';
    }

    const listContainer = document.getElementById('duplicates-list');
    listContainer.textContent = '';
    const loadingP = document.createElement('p');
    loadingP.className = 'placeholder-text';
    loadingP.textContent = 'Searching for duplicates...';
    listContainer.appendChild(loadingP);

    try {
        const res = await fetch(`${API_BASE}${endpoint}`);
        const data = await res.json();

        // Handle checksum-based responses (different structure)
        if (method === 'source-checksum' || method === 'library-checksum') {
            const checksumType = method === 'source-checksum' ? 'sources' : 'library';
            const checksumData = data[checksumType];

            if (!checksumData || !checksumData.exists) {
                listContainer.textContent = '';
                const errorP = document.createElement('p');
                errorP.className = 'placeholder-text';
                errorP.textContent = checksumData?.error || 'Checksum index not found. Generate checksums first from Database section.';
                listContainer.appendChild(errorP);
                document.getElementById('dup-actions').style.display = 'none';
                return;
            }

            duplicatesData = checksumData.duplicate_groups || [];
            duplicateSelection.clear();
            checksumPathSelection.clear();

            document.getElementById('dup-group-count').textContent = duplicatesData.length;

            if (duplicatesData.length > 0) {
                renderChecksumDuplicates(checksumType);
                // Show delete actions for checksum mode (now supported!)
                document.getElementById('dup-actions').style.display = 'flex';
                document.getElementById('selected-count').textContent = '0';
                document.getElementById('delete-selected-dups').disabled = true;

                // Show summary stats
                showToast(`Found ${checksumData.total_duplicate_files} duplicate files (${checksumData.total_wasted_mb?.toFixed(1)} MB wasted)`, 'info');
            } else {
                listContainer.textContent = '';
                const nodupeP = document.createElement('p');
                nodupeP.className = 'placeholder-text';
                nodupeP.textContent = `No duplicates found in ${checksumType} (${checksumData.unique_checksums} unique files)`;
                listContainer.appendChild(nodupeP);
                document.getElementById('dup-actions').style.display = 'none';
            }
        } else {
            // Standard title/hash mode
            duplicatesData = data.duplicate_groups || [];
            duplicateSelection.clear();

            document.getElementById('dup-group-count').textContent = duplicatesData.length;

            if (duplicatesData.length > 0) {
                renderDuplicates();
                document.getElementById('dup-actions').style.display = 'flex';
            } else {
                listContainer.textContent = '';
                const nodupeP = document.createElement('p');
                nodupeP.className = 'placeholder-text';
                nodupeP.textContent = 'No duplicates found';
                listContainer.appendChild(nodupeP);
                document.getElementById('dup-actions').style.display = 'none';
            }
        }

    } catch (error) {
        listContainer.textContent = '';
        const errorP = document.createElement('p');
        errorP.className = 'placeholder-text';
        errorP.textContent = 'Failed to find duplicates';
        listContainer.appendChild(errorP);
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

/**
 * Render checksum-based duplicates (file-based, with delete support)
 * Uses safe DOM methods to avoid XSS vulnerabilities
 */
function renderChecksumDuplicates(checksumType) {
    const listContainer = document.getElementById('duplicates-list');
    listContainer.textContent = '';

    // Store checksumType for delete operation
    listContainer.dataset.checksumType = checksumType;

    duplicatesData.forEach((group, groupIdx) => {
        const groupDiv = document.createElement('div');
        groupDiv.className = 'duplicate-group';

        // Group header
        const headerDiv = document.createElement('div');
        headerDiv.className = 'duplicate-group-header';
        const checksumLabel = group.checksum.substring(0, 12) + '...';
        headerDiv.textContent = `${checksumLabel} (${group.count} copies, ${group.wasted_mb?.toFixed(1) || '?'} MB wasted)`;
        groupDiv.appendChild(headerDiv);

        // Files in this group
        group.files.forEach((file, fileIdx) => {
            const itemDiv = document.createElement('div');
            itemDiv.className = 'duplicate-item' + (file.is_keeper ? ' keep' : '');

            // Badge for keeper, checkbox for duplicates
            if (file.is_keeper) {
                const keepBadge = document.createElement('span');
                keepBadge.className = 'keep-badge';
                keepBadge.textContent = 'KEEP';
                itemDiv.appendChild(keepBadge);
            } else {
                // Checkbox for deletion
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'duplicate-checkbox checksum-checkbox';
                checkbox.dataset.path = file.path;
                checkbox.dataset.group = groupIdx;
                checkbox.addEventListener('change', updateChecksumSelection);
                itemDiv.appendChild(checkbox);
            }

            // File info
            const infoDiv = document.createElement('div');
            infoDiv.className = 'result-info';

            const titleDiv = document.createElement('div');
            titleDiv.className = 'result-title';
            titleDiv.textContent = file.basename;
            infoDiv.appendChild(titleDiv);

            const metaDiv = document.createElement('div');
            metaDiv.className = 'result-meta';

            // Extract author folder from path
            const pathParts = file.path.split('/');
            let authorFolder = '';
            if (checksumType === 'sources') {
                // Sources: /raid0/Audiobooks/Sources/filename.aaxc
                authorFolder = pathParts[pathParts.length - 1]; // Just filename for sources
            } else {
                // Library: /raid0/Audiobooks/Library/Author/Book/file.opus
                if (pathParts.length >= 3) {
                    authorFolder = pathParts[pathParts.length - 3]; // Author folder
                }
            }

            metaDiv.textContent = `${file.size_mb?.toFixed(1) || '?'} MB | ${file.asin || 'No ASIN'} | ${authorFolder}`;
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
    document.querySelectorAll('.checksum-checkbox:checked').forEach(cb => {
        checksumPathSelection.add(cb.dataset.path);
    });

    document.getElementById('selected-count').textContent = checksumPathSelection.size;
    document.getElementById('delete-selected-dups').disabled = checksumPathSelection.size === 0;
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
    // Update the appropriate selection based on mode
    if (currentDupMode === 'source-checksum' || currentDupMode === 'library-checksum') {
        updateChecksumSelection();
    } else {
        updateDuplicateSelection();
    }
}

function deselectAllDuplicates() {
    document.querySelectorAll('.duplicate-checkbox').forEach(cb => {
        cb.checked = false;
    });
    // Update the appropriate selection based on mode
    if (currentDupMode === 'source-checksum' || currentDupMode === 'library-checksum') {
        updateChecksumSelection();
    } else {
        updateDuplicateSelection();
    }
}

async function deleteSelectedDuplicates() {
    // Check which mode we're in
    const isChecksumMode = currentDupMode === 'source-checksum' || currentDupMode === 'library-checksum';

    if (isChecksumMode) {
        // Path-based deletion for checksum duplicates
        if (checksumPathSelection.size === 0) return;

        const checksumType = currentDupMode === 'source-checksum' ? 'sources' : 'library';
        const confirmed = await confirmAction(
            'Delete Duplicates',
            `Are you sure you want to delete ${checksumPathSelection.size} duplicate file(s)?\n\nThis will permanently delete the files${checksumType === 'library' ? ' and remove them from the database' : ''}.`
        );

        if (!confirmed) return;

        showProgress('Deleting Duplicates', `Removing ${checksumPathSelection.size} files...`);

        try {
            const res = await fetch(`${API_BASE}/api/duplicates/delete-by-path`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    paths: Array.from(checksumPathSelection),
                    type: checksumType
                })
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
                showToast(msg, 'success');
                findDuplicates(); // Refresh
                if (checksumType === 'library') {
                    loadDatabaseStats();
                }
            } else {
                showToast(result.error || 'Delete failed', 'error');
            }
        } catch (error) {
            hideProgress();
            showToast('Failed to delete: ' + error.message, 'error');
        }
    } else {
        // ID-based deletion for title/hash duplicates
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
        const result = await safeFetch(`${API_BASE}/api/audiobooks/bulk-update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(bulkSelection),
                field: field,
                value: value
            })
        });

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
        const result = await safeFetch(`${API_BASE}/api/audiobooks/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(bulkSelection),
                delete_files: false
            })
        });

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
            confirmResolve = null;  // Clear before hide so it won't resolve false
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

/**
 * Show confirmation modal with custom callback.
 * Used for actions that need custom handling like delete user.
 */
function showConfirmModal(title, message, onConfirm) {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('confirm-title');
    const bodyEl = document.getElementById('confirm-body');
    const confirmBtn = document.getElementById('confirm-action');

    titleEl.textContent = title;
    bodyEl.textContent = message;
    confirmBtn.textContent = 'Confirm';
    confirmBtn.className = 'office-btn danger';

    modal.classList.add('active');

    // Clone to remove old event listeners
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

    newConfirmBtn.addEventListener('click', () => {
        modal.classList.remove('active');
        if (onConfirm) onConfirm();
    });
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
                } else if (status.result.source_checksums !== undefined) {
                    resultText = `Sources: ${status.result.source_checksums} checksums | Library: ${status.result.library_checksums} checksums`;
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

async function generateChecksumsAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    showProgressModal('Generating Checksums', 'Calculating MD5 checksums for Sources and Library...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/generate-checksums-async`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Checksum generation', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start checksum generation', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start checksum generation: ' + error.message, 'error');
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

        // Update remaining summary box
        const remainingTotal = document.getElementById('remaining-total');
        const sourceTotal = document.getElementById('source-total');
        const summaryBox = document.getElementById('remaining-summary');
        if (remainingTotal && sourceTotal) {
            remainingTotal.textContent = status.remaining.toLocaleString();
            sourceTotal.textContent = status.source_count.toLocaleString();
            // Add complete class when done
            if (summaryBox) {
                summaryBox.classList.toggle('complete', status.remaining === 0);
            }
        }

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

// ============================================
// Audible Sync Section
// ============================================

function initAudibleSection() {
    // Prerequisites check
    document.getElementById('check-audible-prereqs')?.addEventListener('click', checkAudiblePrereqs);

    // Sync operations
    document.getElementById('sync-genres-btn')?.addEventListener('click', syncGenresAsync);
    document.getElementById('sync-narrators-btn')?.addEventListener('click', syncNarratorsAsync);
    document.getElementById('populate-sort-btn')?.addEventListener('click', populateSortFieldsAsync);
    document.getElementById('populate-asins-btn')?.addEventListener('click', populateAsinsAsync);

    // Pipeline operations
    document.getElementById('download-audiobooks-btn')?.addEventListener('click', downloadAudiobooksAsync);
    document.getElementById('rebuild-queue-btn')?.addEventListener('click', rebuildQueueAsync);
    document.getElementById('cleanup-indexes-btn')?.addEventListener('click', cleanupIndexesAsync);

    // Check prerequisites when Audible tab is shown
    document.querySelector('.cabinet-tab[data-section="audible"]')?.addEventListener('click', () => {
        checkAudiblePrereqs();
    });
}

async function checkAudiblePrereqs() {
    const badge = document.getElementById('audible-prereq-badge');
    const icon = document.getElementById('metadata-json-icon');
    const status = document.getElementById('metadata-json-status');
    const help = document.getElementById('audible-prereq-help');

    if (badge) badge.textContent = 'Checking...';
    if (icon) {
        icon.textContent = '⏳';
        icon.className = 'prereq-icon';
    }

    try {
        const res = await fetch(`${API_BASE}/api/utilities/check-audible-prereqs`);
        const result = await res.json();

        if (result.library_metadata_exists) {
            if (badge) badge.textContent = 'Ready';
            if (icon) {
                icon.textContent = '✓';
                icon.className = 'prereq-icon success';
            }
            if (status) status.textContent = `Found: ${result.library_metadata_path}`;
            if (help) help.style.display = 'none';
        } else {
            if (badge) badge.textContent = 'Missing File';
            if (icon) {
                icon.textContent = '✗';
                icon.className = 'prereq-icon error';
            }
            if (status) status.textContent = 'library_metadata.json not found';
            if (help) help.style.display = 'block';
        }
    } catch (error) {
        console.error('Failed to check prerequisites:', error);
        if (badge) badge.textContent = 'Error';
        if (icon) {
            icon.textContent = '⚠';
            icon.className = 'prereq-icon warning';
        }
        if (status) status.textContent = 'Failed to check prerequisites';
    }
}

async function syncGenresAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    const dryRun = document.getElementById('sync-genres-dryrun')?.checked ?? true;

    showProgressModal('Syncing Genres', dryRun ? 'Running in dry-run mode...' : 'Updating genre metadata...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/sync-genres-async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dry_run: dryRun })
        });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Genre sync' + (dryRun ? ' (dry run)' : ''), progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start genre sync', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start genre sync: ' + error.message, 'error');
    }
}

async function syncNarratorsAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    const dryRun = document.getElementById('sync-narrators-dryrun')?.checked ?? true;

    showProgressModal('Updating Narrators', dryRun ? 'Running in dry-run mode...' : 'Updating narrator metadata...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/sync-narrators-async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dry_run: dryRun })
        });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Narrator sync' + (dryRun ? ' (dry run)' : ''), progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start narrator sync', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start narrator sync: ' + error.message, 'error');
    }
}

async function populateSortFieldsAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    const dryRun = document.getElementById('populate-sort-dryrun')?.checked ?? true;

    showProgressModal('Populating Sort Fields', dryRun ? 'Running in dry-run mode...' : 'Generating sort fields...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/populate-sort-fields-async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dry_run: dryRun })
        });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Sort field population' + (dryRun ? ' (dry run)' : ''), progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start sort field population', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start sort field population: ' + error.message, 'error');
    }
}

async function populateAsinsAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    const dryRun = document.getElementById('populate-asins-dryrun')?.checked ?? true;

    showProgressModal('Populating ASINs', dryRun ? 'Running in dry-run mode...' : 'Matching audiobooks to Audible...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/populate-asins-async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dry_run: dryRun })
        });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'ASIN population' + (dryRun ? ' (dry run)' : ''), progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start ASIN population', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start ASIN population: ' + error.message, 'error');
    }
}

async function downloadAudiobooksAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    if (!await confirmAction('Download New Audiobooks',
        'This will download new audiobooks from your Audible account to the Sources folder.\n\nMake sure the audible CLI is configured. Continue?')) {
        return;
    }

    showProgressModal('Downloading Audiobooks', 'Connecting to Audible...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/download-audiobooks-async`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Audiobook download', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start download', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start download: ' + error.message, 'error');
    }
}

async function rebuildQueueAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    showProgressModal('Rebuilding Queue', 'Scanning for unconverted files...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/rebuild-queue-async`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Queue rebuild', progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start queue rebuild', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start queue rebuild: ' + error.message, 'error');
    }
}

async function cleanupIndexesAsync() {
    if (activeOperationId) {
        showToast('An operation is already running', 'error');
        return;
    }

    const dryRun = document.getElementById('cleanup-indexes-dryrun')?.checked ?? true;

    showProgressModal('Cleaning Up Indexes', dryRun ? 'Running in dry-run mode...' : 'Removing stale entries...');

    try {
        const res = await fetch(`${API_BASE}/api/utilities/cleanup-indexes-async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dry_run: dryRun })
        });
        const result = await res.json();

        if (result.success) {
            activeOperationId = result.operation_id;
            showStatusBanner({ id: result.operation_id, description: 'Index cleanup' + (dryRun ? ' (dry run)' : ''), progress: 0 });
            startOperationPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start index cleanup', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start index cleanup: ' + error.message, 'error');
    }
}

// ============================================
// System Administration Section
// ============================================

let upgradePollingInterval = null;

function initSystemSection() {
    // Refresh services button
    document.getElementById('refresh-services')?.addEventListener('click', loadServicesStatus);

    // Start/Stop all buttons
    document.getElementById('start-all-services')?.addEventListener('click', startAllServices);
    document.getElementById('stop-all-services')?.addEventListener('click', stopAllServices);
    document.getElementById('stop-background-services')?.addEventListener('click', stopBackgroundServices);

    // Upgrade source toggle
    document.querySelectorAll('input[name="upgrade-source"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const projectSelector = document.getElementById('project-selector');
            if (projectSelector) {
                projectSelector.style.display = e.target.value === 'project' ? 'block' : 'none';
            }
        });
    });

    // Browse projects button
    document.getElementById('browse-projects')?.addEventListener('click', loadProjectsList);

    // Upgrade buttons
    document.getElementById('check-upgrade')?.addEventListener('click', checkUpgrade);
    document.getElementById('start-upgrade')?.addEventListener('click', startUpgrade);

    // Load initial data when System tab is shown
    document.querySelector('.cabinet-tab[data-section="system"]')?.addEventListener('click', () => {
        loadServicesStatus();
        loadVersionInfo();
        loadPositionSyncStatus();
        loadUsers();
        loadAccessRequests();
    });

    // Position Sync buttons
    document.getElementById('refresh-sync-status')?.addEventListener('click', loadPositionSyncStatus);
    document.getElementById('sync-all-positions')?.addEventListener('click', syncAllPositions);

    // User Management
    initUserManagement();
}

// ============================================
// User Management
// ============================================

function initUserManagement() {
    // Tab switching
    document.querySelectorAll('.user-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;
            document.querySelectorAll('.user-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            document.querySelectorAll('.user-tab-content').forEach(content => {
                content.hidden = content.id !== `${tabName}-tab`;
                content.classList.toggle('active', content.id === `${tabName}-tab`);
            });
        });
    });

    // Refresh users button
    document.getElementById('refresh-users')?.addEventListener('click', () => {
        loadUsers();
        loadAccessRequests();
    });

    // Invite user button
    document.getElementById('invite-user-btn')?.addEventListener('click', showInviteUserModal);
}

function showInviteUserModal() {
    const modal = document.getElementById('invite-user-modal');
    const usernameEl = document.getElementById('invite-username');
    const emailEl = document.getElementById('invite-email');
    const canDownloadEl = document.getElementById('invite-can-download');
    const sendBtn = document.getElementById('invite-user-send');
    const cancelBtn = document.getElementById('invite-user-cancel');
    const closeBtn = document.getElementById('invite-user-close');

    // Reset form
    usernameEl.value = '';
    emailEl.value = '';
    canDownloadEl.checked = true;

    modal.classList.add('active');

    // Clone buttons to remove old event listeners
    const newSendBtn = sendBtn.cloneNode(true);
    const newCancelBtn = cancelBtn.cloneNode(true);
    const newCloseBtn = closeBtn.cloneNode(true);
    sendBtn.parentNode.replaceChild(newSendBtn, sendBtn);
    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
    closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);

    const closeModal = () => modal.classList.remove('active');

    newCancelBtn.addEventListener('click', closeModal);
    newCloseBtn.addEventListener('click', closeModal);

    newSendBtn.addEventListener('click', async () => {
        const username = usernameEl.value.trim();
        const email = emailEl.value.trim();
        const canDownload = canDownloadEl.checked;

        // Validate
        if (!username || username.length < 5) {
            showToast('Username must be at least 5 characters', 'error');
            return;
        }
        if (username.length > 16) {
            showToast('Username must be at most 16 characters', 'error');
            return;
        }
        if (/[<>\\]/.test(username)) {
            showToast('Username cannot contain < > or \\ characters', 'error');
            return;
        }
        if (!email) {
            showToast('Email address is required', 'error');
            return;
        }
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
            showToast('Invalid email format', 'error');
            return;
        }

        // Disable send button
        newSendBtn.disabled = true;
        newSendBtn.textContent = 'Sending...';

        try {
            const res = await fetch(`${API_BASE}/auth/admin/users/invite`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    username: username,
                    email: email,
                    can_download: canDownload
                })
            });

            const data = await res.json();

            if (res.ok) {
                closeModal();
                if (data.email_sent) {
                    showToast(`Invitation sent to ${email}`, 'success');
                } else {
                    // Email failed - show claim token
                    showToast(`User created. Email failed - claim token: ${data.claim_token}`, 'warning');
                }
                loadUsers();
            } else {
                showToast(data.error || 'Failed to invite user', 'error');
            }
        } catch (error) {
            showToast('Connection error', 'error');
        } finally {
            newSendBtn.disabled = false;
            newSendBtn.textContent = 'Send Invitation';
        }
    });
}

async function loadUsers() {
    const userList = document.getElementById('user-list');
    const countBadge = document.getElementById('users-count-badge');
    if (!userList) return;

    // Clear and show loading
    while (userList.firstChild) {
        userList.removeChild(userList.firstChild);
    }
    const loadingP = document.createElement('p');
    loadingP.className = 'placeholder-text';
    loadingP.textContent = 'Loading users...';
    userList.appendChild(loadingP);

    try {
        const res = await fetch(`${API_BASE}/auth/admin/users`, {
            credentials: 'include'
        });

        if (!res.ok) {
            throw new Error('Failed to load users');
        }

        const data = await res.json();

        // Clear loading
        while (userList.firstChild) {
            userList.removeChild(userList.firstChild);
        }

        if (!data.users || data.users.length === 0) {
            const emptyP = document.createElement('p');
            emptyP.className = 'empty-message';
            emptyP.textContent = 'No users found';
            userList.appendChild(emptyP);
            if (countBadge) countBadge.textContent = '0';
            return;
        }

        if (countBadge) countBadge.textContent = data.users.length;

        data.users.forEach(user => {
            const item = createUserItem(user);
            userList.appendChild(item);
        });

    } catch (error) {
        console.error('Error loading users:', error);
        while (userList.firstChild) {
            userList.removeChild(userList.firstChild);
        }
        const errorP = document.createElement('p');
        errorP.className = 'placeholder-text';
        errorP.textContent = 'Failed to load users';
        userList.appendChild(errorP);
    }
}

function createUserItem(user) {
    const item = document.createElement('div');
    item.className = 'user-item';
    item.dataset.userId = user.id;

    // User info
    const info = document.createElement('div');
    info.className = 'user-info';

    const nameRow = document.createElement('div');
    nameRow.className = 'user-name';
    nameRow.textContent = user.username;

    const badges = document.createElement('div');
    badges.className = 'user-badges';

    if (user.is_admin) {
        const adminBadge = document.createElement('span');
        adminBadge.className = 'user-badge admin';
        adminBadge.textContent = 'Admin';
        badges.appendChild(adminBadge);
    }

    const downloadBadge = document.createElement('span');
    downloadBadge.className = `user-badge ${user.can_download ? 'download' : 'no-download'}`;
    downloadBadge.textContent = user.can_download ? 'Download' : 'No Download';
    badges.appendChild(downloadBadge);

    nameRow.appendChild(badges);
    info.appendChild(nameRow);

    const meta = document.createElement('div');
    meta.className = 'user-meta';
    const lastLogin = user.last_login ? new Date(user.last_login).toLocaleString() : 'Never';
    const emailInfo = user.email ? ` • ${user.email}` : '';
    meta.textContent = `Last login: ${lastLogin}${emailInfo}`;
    info.appendChild(meta);

    item.appendChild(info);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'user-actions';

    // Edit user
    const editBtn = document.createElement('button');
    editBtn.className = 'user-action-btn edit';
    editBtn.textContent = 'Edit';
    editBtn.title = `Edit ${user.username}`;
    editBtn.addEventListener('click', () => showEditUserModal(user));
    actions.appendChild(editBtn);

    // Toggle admin status (not for self - can't remove your own admin)
    const adminBtn = document.createElement('button');
    adminBtn.className = `user-action-btn ${user.is_admin ? 'revoke-admin' : 'grant-admin'}`;
    adminBtn.textContent = user.is_admin ? 'Revoke Admin' : 'Make Admin';
    adminBtn.title = user.is_admin ? `Remove admin privileges from ${user.username}` : `Grant admin privileges to ${user.username}`;
    adminBtn.addEventListener('click', () => toggleUserAdmin(user));
    actions.appendChild(adminBtn);

    // Toggle download permission
    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'user-action-btn toggle';
    toggleBtn.textContent = user.can_download ? 'Disable Download' : 'Enable Download';
    toggleBtn.title = `Toggle download permission for ${user.username}`;
    toggleBtn.addEventListener('click', () => toggleUserDownload(user.id, !user.can_download));
    actions.appendChild(toggleBtn);

    // Delete user (not for admins)
    if (!user.is_admin) {
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'user-action-btn delete';
        deleteBtn.textContent = 'Delete';
        deleteBtn.title = `Delete user ${user.username}`;
        deleteBtn.addEventListener('click', () => confirmDeleteUser(user));
        actions.appendChild(deleteBtn);
    }

    item.appendChild(actions);
    return item;
}

async function toggleUserDownload(userId, canDownload) {
    try {
        const res = await fetch(`${API_BASE}/auth/admin/users/${userId}/permissions`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ can_download: canDownload })
        });

        if (res.ok) {
            showToast(`Download permission ${canDownload ? 'enabled' : 'disabled'}`, 'success');
            loadUsers();
        } else {
            const data = await res.json();
            showToast(data.error || 'Failed to update permission', 'error');
        }
    } catch (error) {
        showToast('Connection error', 'error');
    }
}

async function toggleUserAdmin(user) {
    // Confirm the action
    const action = user.is_admin ? 'revoke admin privileges from' : 'grant admin privileges to';
    const confirmed = await new Promise(resolve => {
        showConfirmModal(
            user.is_admin ? 'Revoke Admin' : 'Grant Admin',
            `Are you sure you want to ${action} "${user.username}"?`,
            () => resolve(true),
            () => resolve(false)
        );
    });

    if (!confirmed) return;

    try {
        const res = await fetch(`${API_BASE}/auth/admin/users/${user.id}/toggle-admin`, {
            method: 'POST',
            credentials: 'include'
        });

        if (res.ok) {
            const data = await res.json();
            const status = data.is_admin ? 'granted' : 'revoked';
            showToast(`Admin privileges ${status} for ${user.username}`, 'success');
            loadUsers();
        } else {
            const data = await res.json();
            showToast(data.error || 'Failed to update admin status', 'error');
        }
    } catch (error) {
        showToast('Connection error', 'error');
    }
}

function confirmDeleteUser(user) {
    showConfirmModal(
        'Delete User',
        `Are you sure you want to delete user "${user.username}"? This action cannot be undone.`,
        async () => {
            try {
                const res = await fetch(`${API_BASE}/auth/admin/users/${user.id}`, {
                    method: 'DELETE',
                    credentials: 'include'
                });

                if (res.ok) {
                    showToast(`User ${user.username} deleted`, 'success');
                    loadUsers();
                } else {
                    const data = await res.json();
                    showToast(data.error || 'Failed to delete user', 'error');
                }
            } catch (error) {
                showToast('Connection error', 'error');
            }
        }
    );
}

function showEditUserModal(user, isProfile = false) {
    const modal = document.getElementById('edit-user-modal');
    const titleEl = document.getElementById('edit-user-title');
    const userIdEl = document.getElementById('edit-user-id');
    const usernameEl = document.getElementById('edit-username');
    const emailEl = document.getElementById('edit-email');
    const saveBtn = document.getElementById('edit-user-save');
    const cancelBtn = document.getElementById('edit-user-cancel');
    const closeBtn = document.getElementById('edit-user-close');

    titleEl.textContent = isProfile ? 'Edit Profile' : `Edit User: ${user.username}`;
    userIdEl.value = user.id;
    usernameEl.value = user.username;
    emailEl.value = user.email || '';

    modal.classList.add('active');

    // Clone buttons to remove old event listeners
    const newSaveBtn = saveBtn.cloneNode(true);
    const newCancelBtn = cancelBtn.cloneNode(true);
    const newCloseBtn = closeBtn.cloneNode(true);
    saveBtn.parentNode.replaceChild(newSaveBtn, saveBtn);
    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
    closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);

    const closeModal = () => modal.classList.remove('active');

    newCancelBtn.addEventListener('click', closeModal);
    newCloseBtn.addEventListener('click', closeModal);

    newSaveBtn.addEventListener('click', async () => {
        const newUsername = usernameEl.value.trim();
        const newEmail = emailEl.value.trim();

        if (!newUsername || newUsername.length < 3) {
            showToast('Username must be at least 3 characters', 'error');
            return;
        }
        if (newUsername.length > 32) {
            showToast('Username must be at most 32 characters', 'error');
            return;
        }
        // Check for invalid characters
        if (/[<>\\]/.test(newUsername)) {
            showToast('Username cannot contain < > or \\ characters', 'error');
            return;
        }
        if (newUsername !== newUsername.trim()) {
            showToast('Username cannot have leading or trailing spaces', 'error');
            return;
        }
        // Validate email if provided
        if (newEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newEmail)) {
            showToast('Invalid email format', 'error');
            return;
        }

        try {
            // Use different endpoints for profile vs admin edit
            const endpoint = isProfile
                ? `${API_BASE}/auth/me`
                : `${API_BASE}/auth/admin/users/${user.id}`;

            const res = await fetch(endpoint, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    username: newUsername,
                    email: newEmail || null  // Send null to clear email
                })
            });

            if (res.ok) {
                showToast('User updated successfully', 'success');
                closeModal();
                if (isProfile) {
                    // Update header display
                    const usernameDisplay = document.getElementById('username-display');
                    if (usernameDisplay) usernameDisplay.textContent = newUsername;
                } else {
                    loadUsers();
                }
            } else {
                const data = await res.json();
                showToast(data.error || 'Failed to update user', 'error');
            }
        } catch (error) {
            showToast('Connection error', 'error');
        }
    });
}

async function loadAccessRequests() {
    const requestsList = document.getElementById('requests-list');
    const pendingBadge = document.getElementById('pending-requests-count');
    if (!requestsList) return;

    // Clear and show loading
    while (requestsList.firstChild) {
        requestsList.removeChild(requestsList.firstChild);
    }
    const loadingP = document.createElement('p');
    loadingP.className = 'placeholder-text';
    loadingP.textContent = 'Loading requests...';
    requestsList.appendChild(loadingP);

    try {
        const res = await fetch(`${API_BASE}/auth/admin/access-requests`, {
            credentials: 'include'
        });

        if (!res.ok) {
            throw new Error('Failed to load requests');
        }

        const data = await res.json();

        // Clear loading
        while (requestsList.firstChild) {
            requestsList.removeChild(requestsList.firstChild);
        }

        // Count pending
        const pendingCount = data.requests.filter(r => r.status === 'pending').length;
        if (pendingBadge) {
            pendingBadge.textContent = pendingCount;
            pendingBadge.hidden = pendingCount === 0;
        }

        if (!data.requests || data.requests.length === 0) {
            const emptyP = document.createElement('p');
            emptyP.className = 'empty-message';
            emptyP.textContent = 'No access requests';
            requestsList.appendChild(emptyP);
            return;
        }

        data.requests.forEach(req => {
            const item = createRequestItem(req);
            requestsList.appendChild(item);
        });

    } catch (error) {
        console.error('Error loading requests:', error);
        while (requestsList.firstChild) {
            requestsList.removeChild(requestsList.firstChild);
        }
        const errorP = document.createElement('p');
        errorP.className = 'placeholder-text';
        errorP.textContent = 'Failed to load requests';
        requestsList.appendChild(errorP);
    }
}

function createRequestItem(req) {
    const item = document.createElement('div');
    item.className = 'request-item';
    item.dataset.requestId = req.id;

    // Request info
    const info = document.createElement('div');
    info.className = 'request-info';

    const nameRow = document.createElement('div');
    nameRow.className = 'request-username';
    nameRow.textContent = req.username;

    if (req.has_email) {
        const emailBadge = document.createElement('span');
        emailBadge.className = 'user-badge has-email';
        emailBadge.textContent = 'Has Email';
        emailBadge.title = 'User provided an email for notification';
        nameRow.appendChild(emailBadge);
    }

    info.appendChild(nameRow);

    const meta = document.createElement('div');
    meta.className = 'request-meta';
    const requestedAt = req.requested_at ? new Date(req.requested_at).toLocaleString() : 'Unknown';
    meta.textContent = `Requested: ${requestedAt}`;
    info.appendChild(meta);

    // Status badge
    const statusDiv = document.createElement('div');
    statusDiv.className = 'request-status';

    const statusBadge = document.createElement('span');
    statusBadge.className = `status-badge ${req.status}`;
    statusBadge.textContent = req.status;
    statusDiv.appendChild(statusBadge);

    if (req.status !== 'pending' && req.reviewed_by) {
        const reviewMeta = document.createElement('span');
        reviewMeta.className = 'request-meta';
        reviewMeta.textContent = `by ${req.reviewed_by}`;
        statusDiv.appendChild(reviewMeta);
    }

    info.appendChild(statusDiv);
    item.appendChild(info);

    // Actions (only for pending)
    if (req.status === 'pending') {
        const actions = document.createElement('div');
        actions.className = 'request-actions';

        const approveBtn = document.createElement('button');
        approveBtn.className = 'user-action-btn approve';
        approveBtn.textContent = 'Approve';
        approveBtn.title = `Approve access for ${req.username}`;
        approveBtn.addEventListener('click', () => approveRequest(req.id, req.username));
        actions.appendChild(approveBtn);

        const denyBtn = document.createElement('button');
        denyBtn.className = 'user-action-btn deny';
        denyBtn.textContent = 'Deny';
        denyBtn.title = `Deny access for ${req.username}`;
        denyBtn.addEventListener('click', () => showDenyModal(req.id, req.username));
        actions.appendChild(denyBtn);

        item.appendChild(actions);
    }

    return item;
}

async function approveRequest(requestId, username) {
    try {
        const res = await fetch(`${API_BASE}/auth/admin/access-requests/${requestId}/approve`, {
            method: 'POST',
            credentials: 'include'
        });

        const data = await res.json();

        if (res.ok) {
            let msg = `Access approved for ${username}`;
            if (data.email_sent) {
                msg += ' (notification email sent)';
            }
            showToast(msg, 'success');
            loadUsers();
            loadAccessRequests();
        } else {
            showToast(data.error || 'Failed to approve request', 'error');
        }
    } catch (error) {
        showToast('Connection error', 'error');
    }
}

function showDenyModal(requestId, username) {
    // Create modal content with reason textarea
    const modalBody = document.getElementById('confirm-body');
    while (modalBody.firstChild) {
        modalBody.removeChild(modalBody.firstChild);
    }

    const content = document.createElement('div');
    content.className = 'user-modal-content';

    const message = document.createElement('p');
    message.textContent = `Deny access request from "${username}"?`;
    content.appendChild(message);

    const formGroup = document.createElement('div');
    formGroup.className = 'form-group';

    const label = document.createElement('label');
    label.textContent = 'Reason (optional):';
    formGroup.appendChild(label);

    const textarea = document.createElement('textarea');
    textarea.id = 'deny-reason-input';
    textarea.placeholder = 'Enter reason for denial...';
    formGroup.appendChild(textarea);

    content.appendChild(formGroup);
    modalBody.appendChild(content);

    // Update confirm button
    const confirmBtn = document.getElementById('confirm-action');
    confirmBtn.textContent = 'Deny Request';
    confirmBtn.className = 'office-btn danger';

    // Show modal
    const modal = document.getElementById('confirm-modal');
    const title = document.getElementById('confirm-title');
    title.textContent = 'Deny Access Request';
    modal.classList.add('active');

    // Handle confirm
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
    newConfirmBtn.addEventListener('click', async () => {
        const reason = document.getElementById('deny-reason-input')?.value.trim();
        modal.classList.remove('active');

        try {
            const res = await fetch(`${API_BASE}/auth/admin/access-requests/${requestId}/deny`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ reason: reason || null })
            });

            const data = await res.json();

            if (res.ok) {
                let msg = `Access denied for ${username}`;
                if (data.email_sent) {
                    msg += ' (notification email sent)';
                }
                showToast(msg, 'success');
                loadAccessRequests();
            } else {
                showToast(data.error || 'Failed to deny request', 'error');
            }
        } catch (error) {
            showToast('Connection error', 'error');
        }
    });
}

async function loadServicesStatus() {
    const servicesList = document.getElementById('services-list');
    const statusBadge = document.getElementById('services-status-badge');

    if (!servicesList) return;

    // Clear existing content safely
    while (servicesList.firstChild) {
        servicesList.removeChild(servicesList.firstChild);
    }

    const loadingP = document.createElement('p');
    loadingP.className = 'placeholder-text';
    loadingP.textContent = 'Loading services...';
    servicesList.appendChild(loadingP);

    try {
        const res = await fetch(`${API_BASE}/api/system/services`);
        const data = await res.json();

        // Clear loading message
        while (servicesList.firstChild) {
            servicesList.removeChild(servicesList.firstChild);
        }

        if (!data.services) {
            const errorP = document.createElement('p');
            errorP.className = 'placeholder-text';
            errorP.textContent = 'Failed to load services';
            servicesList.appendChild(errorP);
            return;
        }

        data.services.forEach(service => {
            const serviceDiv = document.createElement('div');
            serviceDiv.className = 'service-item';
            serviceDiv.dataset.service = service.name;

            // Service info section
            const infoDiv = document.createElement('div');
            infoDiv.className = 'service-info';

            const indicator = document.createElement('span');
            indicator.className = `service-status-indicator ${service.active ? 'active' : 'inactive'}`;
            infoDiv.appendChild(indicator);

            const textDiv = document.createElement('div');

            const nameDiv = document.createElement('div');
            nameDiv.className = 'service-name';
            nameDiv.textContent = service.name;
            textDiv.appendChild(nameDiv);

            const statusDiv = document.createElement('div');
            statusDiv.className = 'service-status-text';
            statusDiv.textContent = service.status + (service.enabled ? ' (enabled)' : '');
            textDiv.appendChild(statusDiv);

            infoDiv.appendChild(textDiv);
            serviceDiv.appendChild(infoDiv);

            // Controls section
            const controlsDiv = document.createElement('div');
            controlsDiv.className = 'service-controls';

            if (service.active) {
                const stopBtn = document.createElement('button');
                stopBtn.className = 'service-btn stop';
                stopBtn.title = 'Stop';
                stopBtn.textContent = '⏹';
                stopBtn.addEventListener('click', () => stopService(service.name));
                controlsDiv.appendChild(stopBtn);

                const restartBtn = document.createElement('button');
                restartBtn.className = 'service-btn restart';
                restartBtn.title = 'Restart';
                restartBtn.textContent = '↻';
                restartBtn.addEventListener('click', () => restartService(service.name));
                controlsDiv.appendChild(restartBtn);
            } else {
                const startBtn = document.createElement('button');
                startBtn.className = 'service-btn start';
                startBtn.title = 'Start';
                startBtn.textContent = '▶';
                startBtn.addEventListener('click', () => startService(service.name));
                controlsDiv.appendChild(startBtn);
            }

            serviceDiv.appendChild(controlsDiv);
            servicesList.appendChild(serviceDiv);
        });

        // Update status badge
        if (statusBadge) {
            const activeCount = data.services.filter(s => s.active).length;
            const totalCount = data.services.length;

            statusBadge.textContent = `${activeCount}/${totalCount} running`;
            statusBadge.className = 'badge';

            if (activeCount === totalCount) {
                statusBadge.classList.add('all-running');
            } else if (activeCount > 0) {
                statusBadge.classList.add('partial');
            } else {
                statusBadge.classList.add('error');
            }
        }

    } catch (error) {
        console.error('Failed to load services:', error);
        while (servicesList.firstChild) {
            servicesList.removeChild(servicesList.firstChild);
        }
        const errorP = document.createElement('p');
        errorP.className = 'placeholder-text';
        errorP.textContent = 'Error loading services';
        servicesList.appendChild(errorP);
        showToast('Failed to load services: ' + error.message, 'error');
    }
}

async function startService(serviceName) {
    try {
        showToast(`Starting ${serviceName}...`, 'info');
        const res = await fetch(`${API_BASE}/api/system/services/${serviceName}/start`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            showToast(result.message, 'success');
            loadServicesStatus();
        } else {
            showToast(result.error || 'Failed to start service', 'error');
        }
    } catch (error) {
        showToast('Failed to start service: ' + error.message, 'error');
    }
}

async function stopService(serviceName) {
    try {
        showToast(`Stopping ${serviceName}...`, 'info');
        const res = await fetch(`${API_BASE}/api/system/services/${serviceName}/stop`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            showToast(result.message, 'success');
            loadServicesStatus();
        } else {
            showToast(result.error || 'Failed to stop service', 'error');
        }
    } catch (error) {
        showToast('Failed to stop service: ' + error.message, 'error');
    }
}

async function restartService(serviceName) {
    try {
        showToast(`Restarting ${serviceName}...`, 'info');
        const res = await fetch(`${API_BASE}/api/system/services/${serviceName}/restart`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            showToast(result.message, 'success');
            loadServicesStatus();
        } else {
            showToast(result.error || 'Failed to restart service', 'error');
        }
    } catch (error) {
        showToast('Failed to restart service: ' + error.message, 'error');
    }
}

async function startAllServices() {
    if (!await confirmAction('Start All Services', 'Start all audiobook services?')) {
        return;
    }

    try {
        showToast('Starting all services...', 'info');
        const res = await fetch(`${API_BASE}/api/system/services/start-all`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            showToast('All services started', 'success');
        } else {
            const failures = result.results?.filter(r => !r.success).map(r => r.service).join(', ');
            showToast(`Some services failed to start: ${failures}`, 'error');
        }
        loadServicesStatus();
    } catch (error) {
        showToast('Failed to start services: ' + error.message, 'error');
    }
}

async function stopAllServices() {
    if (!await confirmAction('Stop All Services',
        'This will stop ALL services including the API. You will lose web access and need to restart services via command line. Continue?')) {
        return;
    }

    try {
        showToast('Stopping all services...', 'info');
        const res = await fetch(`${API_BASE}/api/system/services/stop-all?include_api=true`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            showToast('All services stopped. Web access will be lost shortly.', 'success');
        } else {
            showToast('Some services failed to stop', 'error');
        }
        // Don't refresh - we're about to lose connection
    } catch (error) {
        // Expected if API stopped before response
        showToast('Services stopping... connection lost as expected.', 'info');
    }
}

async function stopBackgroundServices() {
    if (!await confirmAction('Stop Background Services',
        'Stop converter, mover, and scanner services? API and proxy will remain running for web access.')) {
        return;
    }

    try {
        showToast('Stopping background services...', 'info');
        const res = await fetch(`${API_BASE}/api/system/services/stop-all`, { method: 'POST' });
        const result = await res.json();

        if (result.success) {
            showToast('Background services stopped', 'success');
        } else {
            showToast('Some services failed to stop', 'error');
        }
        loadServicesStatus();
    } catch (error) {
        showToast('Failed to stop services: ' + error.message, 'error');
    }
}

async function loadVersionInfo() {
    try {
        const res = await fetch(`${API_BASE}/api/system/version`);
        const data = await res.json();

        const versionEl = document.getElementById('current-version');
        const pathEl = document.getElementById('install-path');

        if (versionEl) versionEl.textContent = data.version || 'unknown';
        if (pathEl) pathEl.textContent = data.project_root || '-';
    } catch (error) {
        console.error('Failed to load version:', error);
    }
}

async function loadProjectsList() {
    const projectsList = document.getElementById('available-projects');
    const pathInput = document.getElementById('project-path-input');

    if (!projectsList) return;

    try {
        const res = await fetch(`${API_BASE}/api/system/projects`);
        const data = await res.json();

        // Clear existing content
        while (projectsList.firstChild) {
            projectsList.removeChild(projectsList.firstChild);
        }

        if (!data.projects || data.projects.length === 0) {
            const noProjects = document.createElement('p');
            noProjects.className = 'placeholder-text';
            noProjects.textContent = 'No projects found';
            projectsList.appendChild(noProjects);
            projectsList.style.display = 'block';
            return;
        }

        projectsList.style.display = 'block';

        data.projects.forEach(project => {
            const optionDiv = document.createElement('div');
            optionDiv.className = 'project-option';

            const infoDiv = document.createElement('div');

            const nameDiv = document.createElement('div');
            nameDiv.className = 'project-name';
            nameDiv.textContent = project.name;
            infoDiv.appendChild(nameDiv);

            const pathDiv = document.createElement('div');
            pathDiv.className = 'project-path';
            pathDiv.textContent = project.path;
            infoDiv.appendChild(pathDiv);

            optionDiv.appendChild(infoDiv);

            const versionDiv = document.createElement('div');
            versionDiv.className = 'project-version';
            versionDiv.textContent = project.version || '-';
            optionDiv.appendChild(versionDiv);

            optionDiv.addEventListener('click', () => {
                if (pathInput) pathInput.value = project.path;
                document.querySelectorAll('.project-option').forEach(opt => opt.classList.remove('selected'));
                optionDiv.classList.add('selected');
            });

            projectsList.appendChild(optionDiv);
        });

    } catch (error) {
        console.error('Failed to load projects:', error);
        showToast('Failed to load projects: ' + error.message, 'error');
    }
}

async function checkUpgrade() {
    const sourceRadio = document.querySelector('input[name="upgrade-source"]:checked');
    const source = sourceRadio?.value || 'github';
    const projectPath = document.getElementById('project-path-input')?.value;

    if (source === 'project' && !projectPath) {
        showToast('Please enter or select a project directory', 'error');
        return;
    }

    // Show progress modal
    showProgressModal('Checking for Updates', 'Running dry-run check...');

    try {
        const body = { source };
        if (source === 'project') {
            body.project_path = projectPath;
        }

        const res = await fetch(`${API_BASE}/api/system/upgrade/check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const result = await res.json();

        if (result.success) {
            // Poll for check results
            startCheckPolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start check', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to check for updates: ' + error.message, 'error');
    }
}

function startCheckPolling() {
    // Poll check status every 1 second (check is faster than upgrade)
    const checkPollingInterval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/system/upgrade/status`);
            const status = await res.json();

            // Update progress modal
            const messageEl = document.getElementById('progress-message');
            const outputEl = document.getElementById('progress-output');

            if (messageEl) {
                messageEl.textContent = status.message || 'Checking...';
            }

            if (outputEl && status.output) {
                outputEl.textContent = status.output.join('\n');
                outputEl.scrollTop = outputEl.scrollHeight;
            }

            // Handle completion
            if (!status.running && status.success !== null) {
                clearInterval(checkPollingInterval);

                // Show close button
                const closeBtn = document.getElementById('modal-close-progress');
                if (closeBtn) {
                    closeBtn.style.display = 'inline-flex';
                }

                // Update message based on result
                if (status.result?.upgrade_available) {
                    const current = status.result.current_version || '?';
                    const available = status.result.available_version || '?';
                    if (messageEl) {
                        messageEl.textContent = `▲ Upgrade available: ${current} → ${available}`;
                        messageEl.style.color = 'var(--accent-gold)';
                    }
                    showToast(`Upgrade available: ${current} → ${available}`, 'success');
                } else {
                    if (messageEl) {
                        messageEl.textContent = '✓ Already up to date';
                        messageEl.style.color = 'var(--accent-green)';
                    }
                    showToast('Already up to date', 'success');
                }
            }
        } catch (error) {
            clearInterval(checkPollingInterval);
            hideProgressModal();
            showToast('Error checking status: ' + error.message, 'error');
        }
    }, 1000);
}

async function startUpgrade() {
    const sourceRadio = document.querySelector('input[name="upgrade-source"]:checked');
    const source = sourceRadio?.value || 'github';
    const projectPath = document.getElementById('project-path-input')?.value;

    if (source === 'project' && !projectPath) {
        showToast('Please enter or select a project directory', 'error');
        return;
    }

    const message = source === 'github'
        ? 'This will download and install the latest version from GitHub. The browser will reload when complete. Continue?'
        : `This will install from "${projectPath}". The browser will reload when complete. Continue?`;

    if (!await confirmAction('Start Upgrade', message)) {
        return;
    }

    // Show progress modal
    showProgressModal('Upgrading Application', 'Starting upgrade process...');

    try {
        const body = { source };
        if (source === 'project') {
            body.project_path = projectPath;
        }

        const res = await fetch(`${API_BASE}/api/system/upgrade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const result = await res.json();

        if (result.success) {
            showToast('Upgrade started', 'success');
            startUpgradePolling();
        } else {
            hideProgressModal();
            showToast(result.error || 'Failed to start upgrade', 'error');
        }
    } catch (error) {
        hideProgressModal();
        showToast('Failed to start upgrade: ' + error.message, 'error');
    }
}

function startUpgradePolling() {
    // Poll upgrade status every 2 seconds
    upgradePollingInterval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/system/upgrade/status`);
            const status = await res.json();

            // Update progress modal
            const messageEl = document.getElementById('progress-message');
            const outputEl = document.getElementById('progress-output');

            if (messageEl) {
                messageEl.textContent = status.message || 'Processing...';
            }

            if (outputEl && status.output) {
                outputEl.textContent = status.output.join('\n');
                outputEl.scrollTop = outputEl.scrollHeight;
            }

            // Update stage indicator
            const stageText = {
                'stopping_services': 'Stopping services...',
                'upgrading': 'Installing update...',
                'starting_services': 'Starting services...',
                'restarting_api': 'Restarting API (page will reload)...'
            };

            if (stageText[status.stage] && messageEl) {
                messageEl.textContent = stageText[status.stage];
            }

            // Handle completion
            if (!status.running && status.stage === 'complete') {
                clearInterval(upgradePollingInterval);
                upgradePollingInterval = null;

                if (status.success) {
                    showToast('Upgrade completed! Reloading...', 'success');
                    setTimeout(() => {
                        window.location.reload();
                    }, 2000);
                } else {
                    hideProgressModal();
                    showToast('Upgrade failed: ' + status.message, 'error');
                }
            }

            // Handle API restart - connection will drop
            if (status.stage === 'restarting_api') {
                clearInterval(upgradePollingInterval);
                upgradePollingInterval = null;

                // Wait and then start checking if API is back
                setTimeout(() => {
                    waitForApiRestart();
                }, 3000);
            }

        } catch (error) {
            // Connection lost - API might be restarting
            console.log('Lost connection to API, checking if it restarts...');
            clearInterval(upgradePollingInterval);
            upgradePollingInterval = null;

            // Wait and check if API is back
            setTimeout(() => {
                waitForApiRestart();
            }, 3000);
        }
    }, 2000);
}

async function waitForApiRestart() {
    const messageEl = document.getElementById('progress-message');
    if (messageEl) {
        messageEl.textContent = 'Waiting for API to restart...';
    }

    let attempts = 0;
    const maxAttempts = 30; // 30 attempts * 2 seconds = 60 seconds max wait

    const checkApi = async () => {
        attempts++;
        try {
            const res = await fetch(`${API_BASE}/api/system/version`, {
                method: 'GET',
                signal: AbortSignal.timeout(2000)
            });

            if (res.ok) {
                // API is back!
                hideProgressModal();
                showToast('Upgrade complete! Reloading page...', 'success');
                setTimeout(() => {
                    window.location.reload();
                }, 1500);
                return;
            }
        } catch (error) {
            // Still not ready
        }

        if (attempts < maxAttempts) {
            if (messageEl) {
                messageEl.textContent = `Waiting for API to restart... (${attempts}/${maxAttempts})`;
            }
            setTimeout(checkApi, 2000);
        } else {
            hideProgressModal();
            showToast('API did not restart in time. Please refresh the page manually.', 'error');
        }
    };

    checkApi();
}

// ============================================
// Position Sync
// ============================================

// Unicode icons for position sync status
const SYNC_ICONS = {
    circle: '\u26AA',      // ⚪ (white circle)
    check: '\u2714',       // ✔ (checkmark)
    cross: '\u2716',       // ✖ (X mark)
    warning: '\u26A0'      // ⚠ (warning)
};

async function loadPositionSyncStatus() {
    const badge = document.getElementById('position-sync-badge');
    const statusIcon = document.getElementById('sync-status-icon');
    const statusText = document.getElementById('sync-status-text');
    const statsContainer = document.getElementById('position-sync-stats');
    const syncButton = document.getElementById('sync-all-positions');
    const resultsContainer = document.getElementById('sync-results');

    // Reset to checking state
    if (badge) {
        badge.textContent = 'Checking...';
        badge.className = 'badge';
    }
    if (statusIcon) {
        statusIcon.textContent = SYNC_ICONS.circle;
        statusIcon.className = 'sync-status-icon checking';
    }
    if (statusText) {
        statusText.textContent = 'Checking Audible connection...';
    }
    if (statsContainer) {
        statsContainer.style.display = 'none';
    }
    if (resultsContainer) {
        resultsContainer.style.display = 'none';
    }
    if (syncButton) {
        syncButton.disabled = true;
    }

    try {
        // Fetch status and syncable books in parallel
        const [statusRes, syncableRes] = await Promise.all([
            fetch(`${API_BASE}/api/position/status`),
            fetch(`${API_BASE}/api/position/syncable`)
        ]);

        const status = await statusRes.json();
        const syncable = await syncableRes.json();

        // Check if Audible is available
        if (!status.audible_available) {
            if (badge) {
                badge.textContent = 'Unavailable';
                badge.className = 'badge unavailable';
            }
            if (statusIcon) {
                statusIcon.textContent = SYNC_ICONS.cross;
                statusIcon.className = 'sync-status-icon unavailable';
            }
            if (statusText) {
                statusText.textContent = status.error || 'Audible library not available';
            }
            return;
        }

        if (!status.auth_file_exists) {
            if (badge) {
                badge.textContent = 'Not Configured';
                badge.className = 'badge unavailable';
            }
            if (statusIcon) {
                statusIcon.textContent = SYNC_ICONS.warning;
                statusIcon.className = 'sync-status-icon unavailable';
            }
            if (statusText) {
                statusText.textContent = 'Audible authentication file not found';
            }
            return;
        }

        // Audible is available
        if (badge) {
            badge.textContent = `${syncable.total} books`;
            badge.className = 'badge available';
        }
        if (statusIcon) {
            statusIcon.textContent = SYNC_ICONS.check;
            statusIcon.className = 'sync-status-icon available';
        }
        if (statusText) {
            statusText.textContent = 'Audible sync available and configured';
        }

        // Show stats
        if (statsContainer && syncable.books) {
            statsContainer.style.display = 'grid';

            const totalEl = document.getElementById('sync-total-syncable');
            const withPosEl = document.getElementById('sync-with-positions');
            const lastSyncEl = document.getElementById('sync-last-synced');

            if (totalEl) {
                totalEl.textContent = syncable.total;
            }

            // Count books with positions
            const withPositions = syncable.books.filter(b => b.percent_complete > 0).length;
            if (withPosEl) {
                withPosEl.textContent = withPositions;
            }

            // Find most recent sync
            if (lastSyncEl) {
                const lastSynced = syncable.books
                    .filter(b => b.last_synced)
                    .map(b => new Date(b.last_synced))
                    .sort((a, b) => b - a)[0];

                if (lastSynced) {
                    lastSyncEl.textContent = formatRelativeTime(lastSynced);
                } else {
                    lastSyncEl.textContent = 'Never';
                }
            }
        }

        // Enable sync button if there are syncable books
        if (syncButton && syncable.total > 0) {
            syncButton.disabled = false;
        }

    } catch (error) {
        console.error('Failed to load position sync status:', error);
        if (badge) {
            badge.textContent = 'Error';
            badge.className = 'badge unavailable';
        }
        if (statusIcon) {
            statusIcon.textContent = SYNC_ICONS.cross;
            statusIcon.className = 'sync-status-icon unavailable';
        }
        if (statusText) {
            statusText.textContent = 'Failed to check sync status: ' + error.message;
        }
    }
}

async function syncAllPositions() {
    const syncButton = document.getElementById('sync-all-positions');
    const badge = document.getElementById('position-sync-badge');
    const progressContainer = document.getElementById('sync-progress-container');
    const progressFill = document.getElementById('sync-progress-fill');
    const progressText = document.getElementById('sync-progress-text');
    const progressCount = document.getElementById('sync-progress-count');
    const resultsContainer = document.getElementById('sync-results');

    // Disable button and show syncing state
    if (syncButton) {
        syncButton.disabled = true;
    }
    if (badge) {
        badge.textContent = 'Syncing...';
        badge.className = 'badge syncing';
    }

    // Show progress bar
    if (progressContainer) {
        progressContainer.style.display = 'block';
        if (progressFill) progressFill.style.width = '0%';
        if (progressText) progressText.textContent = 'Starting sync...';
        if (progressCount) progressCount.textContent = '';
    }

    // Hide previous results
    if (resultsContainer) {
        resultsContainer.style.display = 'none';
    }

    try {
        // Show indeterminate progress (we don't have real-time updates from the API)
        if (progressFill) progressFill.style.width = '50%';
        if (progressText) progressText.textContent = 'Syncing with Audible...';

        const result = await safeFetch(`${API_BASE}/api/position/sync-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        // Complete progress bar
        if (progressFill) progressFill.style.width = '100%';

        if (result.error) {
            if (progressText) progressText.textContent = 'Sync failed: ' + result.error;
            showToast('Position sync failed: ' + result.error, 'error');
            return;
        }

        // Show results
        if (progressText) progressText.textContent = 'Sync complete!';
        if (progressCount) progressCount.textContent = `${result.total} books processed`;

        // Update results display
        if (resultsContainer) {
            resultsContainer.style.display = 'grid';

            const pulledEl = document.getElementById('sync-pulled-count');
            const pushedEl = document.getElementById('sync-pushed-count');
            const unchangedEl = document.getElementById('sync-unchanged-count');
            const errorEl = document.getElementById('sync-error-count');

            if (pulledEl) pulledEl.textContent = result.pulled_from_audible || 0;
            if (pushedEl) pushedEl.textContent = result.pushed_to_audible || 0;
            if (unchangedEl) unchangedEl.textContent = result.already_synced || 0;
            if (errorEl) errorEl.textContent = result.failed || 0;
        }

        // Update badge
        if (badge) {
            badge.textContent = `${result.total} synced`;
            badge.className = 'badge available';
        }

        // Show success toast
        const summary = [];
        if (result.pulled_from_audible > 0) summary.push(`${result.pulled_from_audible} pulled`);
        if (result.pushed_to_audible > 0) summary.push(`${result.pushed_to_audible} pushed`);
        if (result.already_synced > 0) summary.push(`${result.already_synced} unchanged`);

        showToast(`Position sync complete: ${summary.join(', ') || 'No changes needed'}`, 'success');

        // Update the syncable books stat without hiding results
        const syncableStat = document.getElementById('syncable-books-count');
        if (syncableStat) {
            syncableStat.textContent = result.total;
        }

        // Hide progress bar after showing results (results stay visible)
        setTimeout(() => {
            if (progressContainer) progressContainer.style.display = 'none';
        }, 3000);

    } catch (error) {
        console.error('Position sync failed:', error);
        if (progressText) progressText.textContent = 'Sync failed: ' + error.message;
        if (badge) {
            badge.textContent = 'Error';
            badge.className = 'badge unavailable';
        }
        showToast('Position sync failed: ' + error.message, 'error');
    } finally {
        // Re-enable button after a short delay
        setTimeout(() => {
            if (syncButton) syncButton.disabled = false;
        }, 2000);
    }
}

function formatRelativeTime(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} min ago`;
    if (diffHours < 24) return `${diffHours} hr ago`;
    if (diffDays < 7) return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`;

    return date.toLocaleDateString();
}
