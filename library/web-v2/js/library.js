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
            sort: 'title',
            order: 'asc'
        };
        this.filters = {
            authors: [],
            narrators: []
        };
        this.narratorCounts = {}; // narrator -> book count
        this.narratorLetterGroup = 'all'; // current letter group filter
        this.narratorSortAsc = true; // A-Z = true, Z-A = false
        this.highlightedNarratorIndex = -1;

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

            // Populate author dropdown
            this.populateSelect('author-filter', this.filters.authors);

            // Load narrator counts for autocomplete
            await this.loadNarratorCounts();

            // Setup narrator autocomplete
            this.setupNarratorAutocomplete();
        } catch (error) {
            console.error('Error loading filters:', error);
        }
    }

    async loadNarratorCounts() {
        try {
            // Get narrator counts from stats endpoint
            const response = await fetch(`${API_BASE}/narrator-counts`);
            if (response.ok) {
                this.narratorCounts = await response.json();
            } else {
                // Fallback: just use narrator list without counts
                this.narratorCounts = {};
                this.filters.narrators.forEach(n => this.narratorCounts[n] = null);
            }
        } catch (error) {
            // Fallback
            this.narratorCounts = {};
            this.filters.narrators.forEach(n => this.narratorCounts[n] = null);
        }
    }

    setupNarratorAutocomplete() {
        const container = document.getElementById('narrator-autocomplete');
        const input = document.getElementById('narrator-search');
        const dropdown = document.getElementById('narrator-dropdown');
        const clearBtn = document.getElementById('narrator-clear');
        const sortBtn = document.getElementById('narrator-sort');

        // Letter group buttons
        container.querySelectorAll('.letter-group').forEach(btn => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.letter-group').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.narratorLetterGroup = btn.dataset.group;
                const query = input.value.toLowerCase().trim();
                this.highlightedNarratorIndex = -1;
                this.showNarratorDropdown(query);
            });
        });

        // Sort toggle button
        sortBtn.addEventListener('click', () => {
            this.narratorSortAsc = !this.narratorSortAsc;
            sortBtn.textContent = this.narratorSortAsc ? 'A-Z' : 'Z-A';
            const query = input.value.toLowerCase().trim();
            this.highlightedNarratorIndex = -1;
            this.showNarratorDropdown(query);
        });

        // Input event - filter narrators as user types
        input.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase().trim();
            this.highlightedNarratorIndex = -1;
            this.showNarratorDropdown(query);
        });

        // Focus event - show dropdown
        input.addEventListener('focus', () => {
            const query = input.value.toLowerCase().trim();
            this.showNarratorDropdown(query);
        });

        // Keyboard navigation
        input.addEventListener('keydown', (e) => {
            const options = dropdown.querySelectorAll('.narrator-option');

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.highlightedNarratorIndex = Math.min(this.highlightedNarratorIndex + 1, options.length - 1);
                this.updateNarratorHighlight(options);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this.highlightedNarratorIndex = Math.max(this.highlightedNarratorIndex - 1, -1);
                this.updateNarratorHighlight(options);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (this.highlightedNarratorIndex >= 0 && options[this.highlightedNarratorIndex]) {
                    options[this.highlightedNarratorIndex].click();
                }
            } else if (e.key === 'Escape') {
                this.hideNarratorDropdown();
                input.blur();
            }
        });

        // Clear button
        clearBtn.addEventListener('click', () => {
            this.selectNarrator('');
        });

        // Click outside to close
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) {
                this.hideNarratorDropdown();
            }
        });
    }

    showNarratorDropdown(query = '') {
        const dropdown = document.getElementById('narrator-dropdown');
        const narrators = this.filters.narrators || [];

        // Filter by letter group first
        let filtered = this.filterByLetterGroup(narrators, this.narratorLetterGroup);

        // Then filter by search query
        if (query) {
            filtered = filtered.filter(n => n.toLowerCase().includes(query));
        }

        // Sort the results
        filtered = this.sortNarrators(filtered, this.narratorSortAsc);

        // Count for this group before limiting
        const groupTotal = filtered.length;

        // Limit display to prevent performance issues
        const maxDisplay = 50;
        const hasMore = filtered.length > maxDisplay;
        filtered = filtered.slice(0, maxDisplay);

        // Build dropdown HTML
        let html = '';

        // "All Narrators" option at top (shows total count for current group)
        const allLabel = this.narratorLetterGroup === 'all' ? 'All Narrators' : `All ${this.narratorLetterGroup.toUpperCase()}`;
        html += `<div class="narrator-option narrator-all-option" data-value="">
            <span>${allLabel}</span>
            <span class="count">${narrators.length} total</span>
        </div>`;

        if (filtered.length === 0 && query) {
            html += `<div class="narrator-no-results">No narrators matching "${query}"</div>`;
        } else if (filtered.length === 0) {
            html += `<div class="narrator-no-results">No narrators in this range</div>`;
        } else {
            filtered.forEach(narrator => {
                const count = this.narratorCounts[narrator];
                const countHtml = count !== null ? `<span class="count">${count}</span>` : '';
                html += `<div class="narrator-option" data-value="${this.escapeHtml(narrator)}">
                    <span>${this.highlightMatch(narrator, query)}</span>
                    ${countHtml}
                </div>`;
            });

            if (hasMore) {
                html += `<div class="narrator-no-results">Showing ${maxDisplay} of ${groupTotal}. Type to filter...</div>`;
            }
        }

        dropdown.innerHTML = html;

        // Add click handlers to options
        dropdown.querySelectorAll('.narrator-option').forEach(option => {
            option.addEventListener('click', () => {
                this.selectNarrator(option.dataset.value);
            });
        });

        dropdown.classList.add('active');
    }

    filterByLetterGroup(narrators, group) {
        if (group === 'all') return [...narrators];

        const ranges = {
            'a-e': ['A', 'B', 'C', 'D', 'E'],
            'f-j': ['F', 'G', 'H', 'I', 'J'],
            'k-o': ['K', 'L', 'M', 'N', 'O'],
            'p-t': ['P', 'Q', 'R', 'S', 'T'],
            'u-z': ['U', 'V', 'W', 'X', 'Y', 'Z']
        };

        const letters = ranges[group] || [];
        return narrators.filter(n => {
            const firstLetter = n.charAt(0).toUpperCase();
            return letters.includes(firstLetter);
        });
    }

    sortNarrators(narrators, ascending) {
        return narrators.sort((a, b) => {
            const cmp = a.localeCompare(b, undefined, { sensitivity: 'base' });
            return ascending ? cmp : -cmp;
        });
    }

    hideNarratorDropdown() {
        const dropdown = document.getElementById('narrator-dropdown');
        dropdown.classList.remove('active');
        this.highlightedNarratorIndex = -1;
    }

    updateNarratorHighlight(options) {
        options.forEach((opt, i) => {
            opt.classList.toggle('highlighted', i === this.highlightedNarratorIndex);
            if (i === this.highlightedNarratorIndex) {
                opt.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    selectNarrator(narrator) {
        const container = document.getElementById('narrator-autocomplete');
        const input = document.getElementById('narrator-search');

        this.currentFilters.narrator = narrator;
        input.value = narrator;

        if (narrator) {
            container.classList.add('has-value');
            input.classList.add('has-value');
        } else {
            container.classList.remove('has-value');
            input.classList.remove('has-value');
        }

        this.hideNarratorDropdown();
        this.currentPage = 1;
        this.loadAudiobooks();
    }

    highlightMatch(text, query) {
        if (!query) return this.escapeHtml(text);
        const escaped = this.escapeHtml(text);
        const regex = new RegExp(`(${this.escapeRegex(query)})`, 'gi');
        return escaped.replace(regex, '<strong>$1</strong>');
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    escapeRegex(str) {
        return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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
                        ./launch.sh
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
        const hasSupplement = book.supplement_count > 0;

        return `
            <div class="book-card" data-id="${book.id}">
                <div class="book-cover">
                    ${book.cover_path ?
                `<img src="/covers/${book.cover_path}" alt="${this.escapeHtml(book.title)}" onerror="this.parentElement.innerHTML='<span class=\\'book-cover-placeholder\\'>📖</span>'">` :
                '<span class="book-cover-placeholder">📖</span>'
            }
                    ${hasSupplement ? `<span class="supplement-badge" title="Has PDF supplement" onclick="event.stopPropagation(); library.showSupplements(${book.id})">PDF</span>` : ''}
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

    async showSupplements(audiobookId) {
        try {
            const response = await fetch(`${API_BASE}/audiobooks/${audiobookId}/supplements`);
            const data = await response.json();

            if (data.supplements && data.supplements.length > 0) {
                // Open the first supplement (typically PDF)
                const supplement = data.supplements[0];
                window.open(`${API_BASE}/supplements/${supplement.id}/download`, '_blank');
            } else {
                alert('No supplements available for this audiobook.');
            }
        } catch (error) {
            console.error('Error loading supplements:', error);
            alert('Error loading supplements.');
        }
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
            // Clear narrator autocomplete
            this.selectNarrator('');
            this.currentFilters = {
                search: '',
                author: '',
                narrator: '',
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

        // Set CORS mode for cross-origin streaming
        this.audio.crossOrigin = 'anonymous';

        // Add error handler for debugging
        this.audio.addEventListener('error', (e) => {
            const error = this.audio.error;
            let message = 'Unknown error';
            if (error) {
                switch (error.code) {
                    case 1: message = 'MEDIA_ERR_ABORTED - Fetching aborted'; break;
                    case 2: message = 'MEDIA_ERR_NETWORK - Network error'; break;
                    case 3: message = 'MEDIA_ERR_DECODE - Decoding error'; break;
                    case 4: message = 'MEDIA_ERR_SRC_NOT_SUPPORTED - Format not supported'; break;
                }
            }
            console.error('Audio error:', message, error);
        });

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

        // Update file info (ID and path)
        document.getElementById('player-id').textContent = `ID: ${book.id}`;
        const pathEl = document.getElementById('player-path');
        pathEl.textContent = book.file_path || 'Unknown path';
        pathEl.title = book.file_path || '';

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

        // Duplicate mode tabs
        this.duplicateMode = 'title'; // Default to title/author mode
        document.querySelectorAll('.mode-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                const mode = e.target.dataset.mode;
                if (mode !== this.duplicateMode) {
                    this.duplicateMode = mode;
                    document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
                    e.target.classList.add('active');
                    this.showDuplicates(mode);
                }
            });
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
        // Commands use relative paths from project root
        const cliCommands = {
            'hash-generate': {
                desc: 'Generate SHA-256 hashes for all audiobooks. This may take several hours for large collections.',
                cmd: 'cd library && python3 scripts/generate_hashes.py'
            },
            'hash-verify': {
                desc: 'Verify a random sample of hashes to check for file corruption.',
                cmd: 'cd library && python3 scripts/generate_hashes.py --verify 20'
            },
            'duplicates-report': {
                desc: 'Generate a detailed duplicate report in the terminal.',
                cmd: 'cd library && python3 scripts/find_duplicates.py'
            },
            'duplicates-json': {
                desc: 'Export duplicate information to a JSON file.',
                cmd: 'cd library && python3 scripts/find_duplicates.py --json -o duplicates.json'
            },
            'duplicates-dryrun': {
                desc: 'Preview which files would be deleted without actually removing them.',
                cmd: 'cd library && python3 scripts/find_duplicates.py --remove'
            },
            'duplicates-execute': {
                desc: 'CAUTION: This will permanently delete duplicate files. The first copy of each audiobook is always protected.',
                cmd: 'cd library && python3 scripts/find_duplicates.py --execute'
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
                    <pre class="cli-command">cd library && python3 scripts/generate_hashes.py</pre>
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
                    <pre class="cli-command">cd library && python3 scripts/generate_hashes.py</pre>
                ` : '<p style="color: #27ae60;">All audiobooks have been hashed!</p>'}
            `;
        } catch (error) {
            content.innerHTML = `<p style="color: #c0392b;">Error loading statistics: ${error.message}</p>`;
        }
    }

    async showDuplicates(mode = null) {
        this.openModal('duplicates-modal');
        this.selectedIds.clear();
        this.updateDeleteButton();

        // Use provided mode or current mode
        if (mode) {
            this.duplicateMode = mode;
        }
        const currentMode = this.duplicateMode || 'title';

        const content = document.getElementById('duplicates-content');
        const summary = document.getElementById('duplicates-summary');
        content.innerHTML = '<div class="loading-spinner"></div><p>Loading duplicates...</p>';
        summary.textContent = 'Loading...';

        try {
            // Choose endpoint based on mode
            const endpoint = currentMode === 'hash' ? 'duplicates' : 'duplicates/by-title';
            const response = await fetch(`${API_BASE}/${endpoint}`);

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to load duplicates');
            }

            const data = await response.json();
            this.duplicateData = data;

            if (data.total_groups === 0) {
                summary.textContent = 'No duplicates found';
                const modeDesc = currentMode === 'hash'
                    ? 'No byte-for-byte identical files found.'
                    : 'No audiobooks with matching title and author found.';
                content.innerHTML = `
                    <div style="text-align: center; padding: 3rem;">
                        <p style="font-size: 1.2rem; color: #27ae60;">No duplicate audiobooks found!</p>
                        <p>${modeDesc}</p>
                    </div>
                `;
                return;
            }

            // Format summary based on mode
            const savingsLabel = currentMode === 'hash' ? 'wasted' : 'potential savings';
            const savingsValue = currentMode === 'hash' ? data.total_wasted_mb : data.total_potential_savings_mb;
            summary.textContent = `${data.total_groups} groups | ${data.total_duplicate_files} duplicates | ${this.formatSize(savingsValue)} ${savingsLabel}`;

            content.innerHTML = data.duplicate_groups.map(group => this.renderDuplicateGroup(group, currentMode)).join('');

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
            const helpText = currentMode === 'hash'
                ? '<p>Make sure hashes have been generated first:</p><pre class="cli-command">cd library && python3 scripts/generate_hashes.py</pre>'
                : '';
            content.innerHTML = `
                <p style="color: #c0392b;">Error loading duplicates: ${error.message}</p>
                ${helpText}
            `;
        }
    }

    renderDuplicateGroup(group, mode = 'hash') {
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
                           ${isKeeper ? 'disabled title="This file is protected - it is the preferred copy"' : ''}>
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

        // Use appropriate label for mode
        const savingsLabel = mode === 'hash' ? 'Wasted' : 'Savings';
        const savingsValue = mode === 'hash' ? group.wasted_mb : group.potential_savings_mb;

        return `
            <div class="duplicate-group">
                <div class="duplicate-group-header">
                    <span class="duplicate-group-title">${this.escapeHtml(group.title || group.files[0]?.title || 'Unknown')}</span>
                    <span class="duplicate-group-meta">
                        ${group.count} copies | ${savingsLabel}: ${this.formatSize(savingsValue)}
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
                body: JSON.stringify({
                    audiobook_ids: Array.from(this.selectedIds),
                    mode: this.duplicateMode || 'title'
                })
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
