// Modern Audiobook Library - API-backed with pagination
const API_BASE = 'http://localhost:5001/api';

class AudiobookLibraryV2 {
    constructor() {
        this.currentPage = 1;
        this.perPage = 50;
        this.totalPages = 1;
        this.totalCount = 0;
        this.currentFilters = {
            search: '',
            author: '',
            narrator: '',
            format: '',
            sort: 'title',
            order: 'asc'
        };
        this.filters = {
            authors: [],
            narrators: [],
            formats: []
        };

        this.init();
    }

    async init() {
        await this.loadStats();
        await this.loadFilters();
        this.setupEventListeners();
        await this.loadAudiobooks();
    }

    showLoading(show = true) {
        const overlay = document.getElementById('loading-overlay');
        if (show) {
            overlay.classList.add('active');
        } else {
            overlay.classList.remove('active');
        }
    }

    async loadStats() {
        try {
            const response = await fetch(`${API_BASE}/stats`);
            const stats = await response.json();

            document.getElementById('total-books').textContent = stats.total_audiobooks.toLocaleString();
            document.getElementById('total-hours').textContent = stats.total_hours.toLocaleString();
            document.getElementById('total-authors').textContent = stats.unique_authors.toLocaleString();
            document.getElementById('total-narrators').textContent = stats.unique_narrators.toLocaleString();
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    async loadFilters() {
        try {
            const response = await fetch(`${API_BASE}/filters`);
            this.filters = await response.json();

            // Populate filter dropdowns
            this.populateSelect('author-filter', this.filters.authors);
            this.populateSelect('narrator-filter', this.filters.narrators);
            this.populateSelect('format-filter', this.filters.formats);
        } catch (error) {
            console.error('Error loading filters:', error);
        }
    }

    populateSelect(selectId, options) {
        const select = document.getElementById(selectId);
        const currentValue = select.value;

        // Keep the "All" option
        const allOption = select.options[0];

        // Clear existing options except first
        select.innerHTML = '';
        select.appendChild(allOption);

        // Add new options
        options.forEach(option => {
            const optionEl = document.createElement('option');
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
                per_page: this.perPage
            });

            if (this.currentFilters.search) params.append('search', this.currentFilters.search);
            if (this.currentFilters.author) params.append('author', this.currentFilters.author);
            if (this.currentFilters.narrator) params.append('narrator', this.currentFilters.narrator);
            if (this.currentFilters.format) params.append('format', this.currentFilters.format);
            if (this.currentFilters.sort) params.append('sort', this.currentFilters.sort);
            if (this.currentFilters.order) params.append('order', this.currentFilters.order);

            const response = await fetch(`${API_BASE}/audiobooks?${params}`);
            const data = await response.json();

            this.totalPages = data.pagination.total_pages;
            this.totalCount = data.pagination.total_count;

            this.renderBooks(data.audiobooks);
            this.renderPagination(data.pagination);
            this.updateResultsInfo(data.pagination);

            // Scroll to top
            window.scrollTo({ top: 0, behavior: 'smooth' });
        } catch (error) {
            console.error('Error loading audiobooks:', error);
            document.getElementById('books-grid').innerHTML = `
                <p style="color: var(--parchment); text-align: center; grid-column: 1/-1;">
                    Error loading audiobooks. Please ensure the API server is running.
                    <br><br>
                    Run: <code style="background: var(--wood-dark); padding: 0.5rem; border-radius: 4px;">
                        cd /raid0/ClaudeCodeProjects/audiobook-library && source venv/bin/activate && python backend/api.py
                    </code>
                </p>
            `;
        } finally {
            this.showLoading(false);
        }
    }

    renderBooks(books) {
        const grid = document.getElementById('books-grid');

        if (books.length === 0) {
            grid.innerHTML = `
                <p style="color: var(--parchment); text-align: center; grid-column: 1/-1;">
                    No audiobooks found matching your filters.
                </p>
            `;
            return;
        }

        grid.innerHTML = books.map(book => this.createBookCard(book)).join('');

        // Add click event listeners to play audiobooks
        books.forEach(book => {
            const card = grid.querySelector(`.book-card[data-id="${book.id}"]`);
            if (card && audioPlayer) {
                card.addEventListener('click', () => {
                    audioPlayer.playAudiobook(book);
                });
            }
        });
    }

    createBookCard(book) {
        const formatQuality = book.format ? book.format.toUpperCase() : 'M4B';
        const quality = book.quality ? ` ${book.quality}` : '';

        return `
            <div class="book-card" data-id="${book.id}">
                <div class="book-cover">
                    ${book.cover_path ?
                `<img src="/covers/${book.cover_path}" alt="${this.escapeHtml(book.title)}" onerror="this.parentElement.innerHTML='<span class=\\'book-cover-placeholder\\'>📖</span>'">` :
                '<span class="book-cover-placeholder">📖</span>'
            }
                </div>
                <div class="book-title">${this.escapeHtml(book.title)}</div>
                ${book.author ? `<div class="book-author">by ${this.escapeHtml(book.author)}</div>` : ''}
                ${book.narrator ? `<div class="book-narrator">Narrated by ${this.escapeHtml(book.narrator)}</div>` : ''}
                <div class="book-meta">
                    <span class="book-format">${formatQuality}${quality}</span>
                    <span class="book-duration">${book.duration_formatted || `${Math.round(book.duration_hours || 0)}h`}</span>
                </div>
            </div>
        `;
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    updateResultsInfo(pagination) {
        const start = (pagination.page - 1) * pagination.per_page + 1;
        const end = Math.min(pagination.page * pagination.per_page, pagination.total_count);

        document.getElementById('showing-count').textContent =
            `Showing ${start}-${end} of ${pagination.total_count.toLocaleString()} audiobooks`;
    }

    renderPagination(pagination) {
        const html = this.createPaginationHTML(pagination);
        document.getElementById('top-pagination').innerHTML = html;
        document.getElementById('bottom-pagination').innerHTML = html;
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
                pages = [1, 2, 3, 4, 5, '...', pagination.total_pages];
            } else if (pagination.page >= pagination.total_pages - 3) {
                pages = [1, '...', ...Array.from({ length: 5 }, (_, i) => pagination.total_pages - 4 + i)];
            } else {
                pages = [1, '...', pagination.page - 1, pagination.page, pagination.page + 1, '...', pagination.total_pages];
            }
        }

        let html = `
            <button class="pagination-btn" onclick="library.goToPage(${pagination.page - 1})"
                    ${!pagination.has_prev ? 'disabled' : ''}>
                ← Prev
            </button>
        `;

        pages.forEach(page => {
            if (page === '...') {
                html += '<span style="padding: 0 0.5rem; color: var(--parchment);">...</span>';
            } else {
                html += `
                    <button class="pagination-btn ${page === pagination.page ? 'active' : ''}"
                            onclick="library.goToPage(${page})">
                        ${page}
                    </button>
                `;
            }
        });

        html += `
            <button class="pagination-btn" onclick="library.goToPage(${pagination.page + 1})"
                    ${!pagination.has_next ? 'disabled' : ''}>
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

    setupEventListeners() {
        // Search input with debounce
        let searchTimeout;
        document.getElementById('search-input').addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                this.currentFilters.search = e.target.value.trim();
                this.currentPage = 1;
                this.loadAudiobooks();
            }, 500);
        });

        // Clear search
        document.getElementById('clear-search').addEventListener('click', () => {
            document.getElementById('search-input').value = '';
            document.getElementById('author-filter').value = '';
            document.getElementById('narrator-filter').value = '';
            document.getElementById('format-filter').value = '';
            this.currentFilters = {
                search: '',
                author: '',
                narrator: '',
                format: '',
                sort: 'title',
                order: 'asc'
            };
            this.currentPage = 1;
            this.loadAudiobooks();
        });

        // Author filter
        document.getElementById('author-filter').addEventListener('change', (e) => {
            this.currentFilters.author = e.target.value;
            this.currentPage = 1;
            this.loadAudiobooks();
        });

        // Narrator filter
        document.getElementById('narrator-filter').addEventListener('change', (e) => {
            this.currentFilters.narrator = e.target.value;
            this.currentPage = 1;
            this.loadAudiobooks();
        });

        // Format filter
        document.getElementById('format-filter').addEventListener('change', (e) => {
            this.currentFilters.format = e.target.value;
            this.currentPage = 1;
            this.loadAudiobooks();
        });

        // Sort filter
        document.getElementById('sort-filter').addEventListener('change', (e) => {
            const [sort, order] = e.target.value.split(':');
            this.currentFilters.sort = sort;
            this.currentFilters.order = order;
            this.currentPage = 1;
            this.loadAudiobooks();
        });

        // Per page
        document.getElementById('per-page').addEventListener('change', (e) => {
            this.perPage = parseInt(e.target.value);
            this.currentPage = 1;
            this.loadAudiobooks();
        });

        // Refresh button
        document.getElementById('refresh-btn').addEventListener('click', async () => {
            await this.refreshLibrary();
        });
    }

    async refreshLibrary() {
        const refreshBtn = document.getElementById('refresh-btn');
        refreshBtn.disabled = true;
        refreshBtn.textContent = '↻ Refreshing...';

        try {
            // Reload stats and filters
            await this.loadStats();
            await this.loadFilters();

            // Reload current page
            await this.loadAudiobooks();

            alert('Library refreshed successfully!');
        } catch (error) {
            console.error('Error refreshing library:', error);
            alert('Failed to refresh library. Please check the console for details.');
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '↻ Refresh';
        }
    }
}

// Initialize library when DOM is loaded
let library;
document.addEventListener('DOMContentLoaded', () => {
    library = new AudiobookLibraryV2();
});

// Audio Player Class
class AudioPlayer {
    constructor() {
        this.player = document.getElementById('audio-player');
        this.audio = document.getElementById('audio-element');
        this.currentBook = null;
        this.playbackRates = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0];
        this.currentRateIndex = 1; // Start at 1.0x

        this.setupEventListeners();
    }

    setupEventListeners() {
        // Close button
        document.getElementById('close-player').addEventListener('click', () => {
            this.close();
        });

        // Play/Pause
        document.getElementById('play-pause').addEventListener('click', () => {
            this.togglePlayPause();
        });

        // Rewind/Forward
        document.getElementById('rewind').addEventListener('click', () => {
            this.audio.currentTime = Math.max(0, this.audio.currentTime - 30);
        });

        document.getElementById('forward').addEventListener('click', () => {
            this.audio.currentTime = Math.min(this.audio.duration, this.audio.currentTime + 30);
        });

        // Speed control
        document.getElementById('speed-btn').addEventListener('click', () => {
            this.cyclePlaybackSpeed();
        });

        // Volume
        document.getElementById('volume').addEventListener('input', (e) => {
            this.audio.volume = e.target.value / 100;
        });

        // Progress bar
        document.getElementById('progress-bar').addEventListener('input', (e) => {
            const time = (e.target.value / 100) * this.audio.duration;
            this.audio.currentTime = time;
        });

        // Audio events
        this.audio.addEventListener('timeupdate', () => {
            this.updateProgress();
        });

        this.audio.addEventListener('loadedmetadata', () => {
            this.updateTotalTime();
        });

        this.audio.addEventListener('ended', () => {
            document.getElementById('play-pause').textContent = '▶';
        });

        this.audio.addEventListener('play', () => {
            document.getElementById('play-pause').textContent = '⏸';
        });

        this.audio.addEventListener('pause', () => {
            document.getElementById('play-pause').textContent = '▶';
        });
    }

    async playAudiobook(book) {
        this.currentBook = book;

        // Update player UI
        document.getElementById('player-title').textContent = book.title;
        document.getElementById('player-author').textContent = book.author || 'Unknown Author';

        const coverImg = document.getElementById('player-cover');
        if (book.cover_path) {
            coverImg.src = '/covers/' + book.cover_path;
            coverImg.alt = book.title;
        } else {
            coverImg.src = '';
            coverImg.alt = '';
        }

        // Load audio file
        this.audio.src = 'http://localhost:5001/api/stream/' + book.id;
        this.audio.playbackRate = this.playbackRates[this.currentRateIndex];

        // Show player
        this.player.style.display = 'block';

        // Try to play
        try {
            await this.audio.play();
        } catch (error) {
            console.error('Failed to play audio:', error);
            alert('Failed to load audio file. Please check the console for details.');
        }
    }

    togglePlayPause() {
        if (this.audio.paused) {
            this.audio.play();
        } else {
            this.audio.pause();
        }
    }

    cyclePlaybackSpeed() {
        this.currentRateIndex = (this.currentRateIndex + 1) % this.playbackRates.length;
        const newRate = this.playbackRates[this.currentRateIndex];
        this.audio.playbackRate = newRate;
        document.getElementById('playback-speed').textContent = newRate + 'x';
    }

    updateProgress() {
        if (!this.audio.duration) return;

        const progress = (this.audio.currentTime / this.audio.duration) * 100;
        document.getElementById('progress-bar').value = progress;

        // Update current time
        const minutes = Math.floor(this.audio.currentTime / 60);
        const seconds = Math.floor(this.audio.currentTime % 60);
        document.getElementById('current-time').textContent =
            minutes + ':' + seconds.toString().padStart(2, '0');
    }

    updateTotalTime() {
        const hours = Math.floor(this.audio.duration / 3600);
        const minutes = Math.floor((this.audio.duration % 3600) / 60);
        const seconds = Math.floor(this.audio.duration % 60);

        let timeStr;
        if (hours > 0) {
            timeStr = hours + ':' + minutes.toString().padStart(2, '0') + ':' + seconds.toString().padStart(2, '0');
        } else {
            timeStr = minutes + ':' + seconds.toString().padStart(2, '0');
        }

        document.getElementById('total-time').textContent = timeStr;
    }

    close() {
        this.audio.pause();
        this.player.style.display = 'none';
        this.currentBook = null;
    }
}

// Initialize player when DOM is loaded
let audioPlayer;
document.addEventListener('DOMContentLoaded', () => {
    audioPlayer = new AudioPlayer();
});


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
        const dropdownBtn = document.getElementById('duplicates-btn');
        const dropdownContent = document.getElementById('duplicates-menu');

        dropdownBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdownContent.classList.toggle('show');
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', () => {
            dropdownContent?.classList.remove('show');
        });

        // Dropdown menu items
        dropdownContent?.addEventListener('click', (e) => {
            const action = e.target.dataset.action;
            if (action) {
                e.preventDefault();
                dropdownContent.classList.remove('show');
                this.handleAction(action);
            }
        });

        // Modal close buttons
        document.querySelectorAll('.modal-close').forEach(btn => {
            btn.addEventListener('click', () => {
                const modalId = btn.dataset.modal;
                this.closeModal(modalId);
            });
        });

        // Close modals when clicking backdrop
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.classList.remove('show');
                }
            });
        });

        // Toolbar buttons
        document.getElementById('select-all-duplicates')?.addEventListener('click', () => {
            this.selectAllDuplicates();
        });

        document.getElementById('deselect-all')?.addEventListener('click', () => {
            this.deselectAll();
        });

        document.getElementById('delete-selected')?.addEventListener('click', () => {
            this.confirmDelete();
        });

        // Confirmation modal
        document.getElementById('confirm-cancel')?.addEventListener('click', () => {
            this.closeModal('confirm-modal');
        });

        document.getElementById('confirm-delete')?.addEventListener('click', () => {
            this.executeDelete();
        });

        // Copy CLI command
        document.getElementById('copy-cli-command')?.addEventListener('click', () => {
            const cmd = document.getElementById('cli-command').textContent;
            navigator.clipboard.writeText(cmd).then(() => {
                const btn = document.getElementById('copy-cli-command');
                btn.textContent = 'Copied!';
                setTimeout(() => btn.textContent = 'Copy to Clipboard', 2000);
            });
        });
    }

    handleAction(action) {
        const cliCommands = {
            'hash-generate': {
                desc: 'Generate SHA-256 hashes for all audiobooks. This may take several hours for large collections.',
                cmd: 'cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/generate_hashes.py'
            },
            'hash-verify': {
                desc: 'Verify a random sample of hashes to check for file corruption.',
                cmd: 'cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/generate_hashes.py --verify 20'
            },
            'duplicates-report': {
                desc: 'Generate a detailed duplicate report in the terminal.',
                cmd: 'cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/find_duplicates.py'
            },
            'duplicates-json': {
                desc: 'Export duplicate information to a JSON file.',
                cmd: 'cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/find_duplicates.py --json -o duplicates.json'
            },
            'duplicates-dryrun': {
                desc: 'Preview which files would be deleted without actually removing them.',
                cmd: 'cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/find_duplicates.py --remove'
            },
            'duplicates-execute': {
                desc: 'CAUTION: This will permanently delete duplicate files. The first copy of each audiobook is always protected.',
                cmd: 'cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/find_duplicates.py --execute'
            }
        };

        switch (action) {
            case 'hash-stats':
                this.showHashStats();
                break;
            case 'show-duplicates':
                this.showDuplicates();
                break;
            default:
                if (cliCommands[action]) {
                    this.showCLICommand(cliCommands[action].desc, cliCommands[action].cmd);
                }
        }
    }

    openModal(modalId) {
        document.getElementById(modalId)?.classList.add('show');
    }

    closeModal(modalId) {
        document.getElementById(modalId)?.classList.remove('show');
    }

    showCLICommand(description, command) {
        document.getElementById('cli-description').textContent = description;
        document.getElementById('cli-command').textContent = command;
        this.openModal('cli-modal');
    }

    async showHashStats() {
        this.openModal('hash-stats-modal');
        const content = document.getElementById('hash-stats-content');
        content.innerHTML = '<div class="loading-spinner"></div><p>Loading statistics...</p>';

        try {
            const response = await fetch(`${API_BASE}/hash-stats`);
            const stats = await response.json();

            if (!stats.hash_column_exists) {
                content.innerHTML = `
                    <p>Hash column not found in database. Run hash generation first:</p>
                    <pre class="cli-command">cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/generate_hashes.py</pre>
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
                ${stats.unhashed_count > 0 ? `
                    <p>To generate remaining hashes, run:</p>
                    <pre class="cli-command">cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/generate_hashes.py</pre>
                ` : '<p style="color: #27ae60;">All audiobooks have been hashed!</p>'}
            `;
        } catch (error) {
            content.innerHTML = `<p style="color: #c0392b;">Error loading statistics: ${error.message}</p>`;
        }
    }

    async showDuplicates() {
        this.openModal('duplicates-modal');
        this.selectedIds.clear();
        this.updateDeleteButton();

        const content = document.getElementById('duplicates-content');
        const summary = document.getElementById('duplicates-summary');
        content.innerHTML = '<div class="loading-spinner"></div><p>Loading duplicates...</p>';
        summary.textContent = 'Loading...';

        try {
            const response = await fetch(`${API_BASE}/duplicates`);

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to load duplicates');
            }

            const data = await response.json();
            this.duplicateData = data;

            if (data.total_groups === 0) {
                summary.textContent = 'No duplicates found';
                content.innerHTML = `
                    <div style="text-align: center; padding: 3rem;">
                        <p style="font-size: 1.2rem; color: #27ae60;">No duplicate audiobooks found!</p>
                        <p>All your audiobooks are unique.</p>
                    </div>
                `;
                return;
            }

            summary.textContent = `${data.total_groups} groups | ${data.total_duplicate_files} duplicates | ${this.formatSize(data.total_wasted_mb)} wasted`;

            content.innerHTML = data.duplicate_groups.map(group => this.renderDuplicateGroup(group)).join('');

            // Add checkbox event listeners
            content.querySelectorAll('.duplicate-checkbox').forEach(checkbox => {
                checkbox.addEventListener('change', (e) => {
                    const id = parseInt(e.target.dataset.id);
                    const row = e.target.closest('.duplicate-file');

                    if (e.target.checked) {
                        this.selectedIds.add(id);
                        row.classList.add('selected');
                    } else {
                        this.selectedIds.delete(id);
                        row.classList.remove('selected');
                    }
                    this.updateDeleteButton();
                });
            });

        } catch (error) {
            summary.textContent = 'Error';
            content.innerHTML = `
                <p style="color: #c0392b;">Error loading duplicates: ${error.message}</p>
                <p>Make sure hashes have been generated first:</p>
                <pre class="cli-command">cd /raid0/ClaudeCodeProjects/audiobook-library && python3 scripts/generate_hashes.py</pre>
            `;
        }
    }

    renderDuplicateGroup(group) {
        const filesHtml = group.files.map(file => {
            const isKeeper = file.is_keeper;
            const badgeClass = isKeeper ? 'badge-keep' : 'badge-duplicate';
            const badgeText = isKeeper ? 'KEEP' : 'DUPLICATE';
            const rowClass = isKeeper ? 'keeper' : 'deletable';

            return `
                <div class="duplicate-file ${rowClass}" data-id="${file.id}">
                    <input type="checkbox"
                           class="duplicate-checkbox"
                           data-id="${file.id}"
                           ${isKeeper ? 'disabled title="This file is protected - it is the original copy"' : ''}>
                    <div class="duplicate-info">
                        <div class="duplicate-title">${this.escapeHtml(file.title)}</div>
                        <div class="duplicate-path">${this.escapeHtml(file.file_path)}</div>
                    </div>
                    <div class="duplicate-meta">
                        <span>${file.format?.toUpperCase() || 'N/A'}</span>
                        <span>${file.duration_formatted || 'N/A'}</span>
                        <span>${this.formatSize(file.file_size_mb)}</span>
                    </div>
                    <span class="duplicate-badge ${badgeClass}">${badgeText}</span>
                </div>
            `;
        }).join('');

        return `
            <div class="duplicate-group">
                <div class="duplicate-group-header">
                    <span class="duplicate-group-title">${this.escapeHtml(group.files[0]?.title || 'Unknown')}</span>
                    <span class="duplicate-group-meta">
                        ${group.count} copies | Wasted: ${this.formatSize(group.wasted_mb)}
                    </span>
                </div>
                ${filesHtml}
            </div>
        `;
    }

    selectAllDuplicates() {
        document.querySelectorAll('.duplicate-checkbox:not(:disabled)').forEach(checkbox => {
            checkbox.checked = true;
            const id = parseInt(checkbox.dataset.id);
            this.selectedIds.add(id);
            checkbox.closest('.duplicate-file').classList.add('selected');
        });
        this.updateDeleteButton();
    }

    deselectAll() {
        document.querySelectorAll('.duplicate-checkbox').forEach(checkbox => {
            checkbox.checked = false;
            checkbox.closest('.duplicate-file').classList.remove('selected');
        });
        this.selectedIds.clear();
        this.updateDeleteButton();
    }

    updateDeleteButton() {
        const btn = document.getElementById('delete-selected');
        if (btn) {
            btn.textContent = `Delete Selected (${this.selectedIds.size})`;
            btn.disabled = this.selectedIds.size === 0;
        }
    }

    confirmDelete() {
        if (this.selectedIds.size === 0) return;

        const content = document.getElementById('confirm-content');
        content.innerHTML = `
            <p>You are about to permanently delete <strong>${this.selectedIds.size}</strong> audiobook file(s).</p>
            <p style="color: #c0392b;"><strong>This action cannot be undone!</strong></p>
            <p>The system will automatically protect the last copy of each audiobook to prevent data loss.</p>
        `;

        this.openModal('confirm-modal');
    }

    async executeDelete() {
        this.closeModal('confirm-modal');

        const btn = document.getElementById('delete-selected');
        btn.disabled = true;
        btn.textContent = 'Deleting...';

        try {
            const response = await fetch(`${API_BASE}/duplicates/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audiobook_ids: Array.from(this.selectedIds) })
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
                alert('Error: ' + (result.error || 'Unknown error'));
            }

        } catch (error) {
            alert('Error deleting files: ' + error.message);
        }

        this.updateDeleteButton();
    }

    formatSize(mb) {
        if (mb >= 1024) {
            return (mb / 1024).toFixed(1) + ' GB';
        }
        return mb.toFixed(1) + ' MB';
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize duplicate manager
let duplicateManager;
document.addEventListener('DOMContentLoaded', () => {
    duplicateManager = new DuplicateManager();
});
