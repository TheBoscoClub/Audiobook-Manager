// Audiobook Library - Interactive Functionality

class AudiobookLibrary {
    constructor() {
        this.allBooks = [];
        this.filteredBooks = [];
        this.currentView = 'grid';
        this.currentSort = 'title';
        this.currentPage = 1;
        this.booksPerPage = 24;
        this.activeFilters = {
            search: '',
            author: null,
            genre: null,
            era: null,
            publisher: null,
            narrator: null,
            series: null,
            topic: null
        };
        this.init();
    }

    async init() {
        await this.loadAudiobooks();
        this.setupEventListeners();
        this.buildIndices();
        this.applyFilters();
        this.renderBooks();
    }

    async loadAudiobooks() {
        try {
            const response = await fetch('../data/audiobooks.json');
            const data = await response.json();
            this.allBooks = data.audiobooks;
            this.updateStats(data.total_audiobooks, this.allBooks.reduce((sum, book) => sum + book.duration_hours, 0));
        } catch (error) {
            console.error('Error loading audiobooks:', error);
            document.getElementById('showing-count').textContent = 'Error loading audiobooks. Please run the scanner first.';
        }
    }

    updateStats(totalBooks, totalHours) {
        document.getElementById('total-books').textContent = `${totalBooks.toLocaleString()} volumes`;
        document.getElementById('total-hours').textContent = `${Math.round(totalHours).toLocaleString()} hours`;
    }

    async refreshLibrary() {
        const refreshBtn = document.getElementById('refresh-btn');
        refreshBtn.classList.add('refreshing');
        refreshBtn.disabled = true;

        try {
            // Add timestamp to bypass cache
            const response = await fetch(`../data/audiobooks.json?t=${Date.now()}`);
            const data = await response.json();

            const oldCount = this.allBooks.length;
            this.allBooks = data.audiobooks;
            const newCount = this.allBooks.length;

            // Rebuild indices and refresh display
            this.buildIndices();
            this.applyFilters();
            this.renderBooks();

            // Show notification
            const diff = newCount - oldCount;
            if (diff > 0) {
                alert(`Library refreshed! Found ${diff} new audiobook${diff > 1 ? 's' : ''}.`);
            } else if (diff < 0) {
                alert(`Library refreshed! ${Math.abs(diff)} audiobook${Math.abs(diff) > 1 ? 's' : ''} removed.`);
            } else {
                alert('Library refreshed! No changes detected.');
            }
        } catch (error) {
            console.error('Error refreshing library:', error);
            alert('Failed to refresh library. Please check the console for details.');
        } finally {
            refreshBtn.classList.remove('refreshing');
            refreshBtn.disabled = false;
        }
    }

    setupEventListeners() {
        // Search
        const searchInput = document.getElementById('search-input');
        searchInput.addEventListener('input', (e) => {
            this.activeFilters.search = e.target.value.toLowerCase();
            this.applyFilters();
        });

        document.getElementById('clear-search').addEventListener('click', () => {
            searchInput.value = '';
            this.activeFilters.search = '';
            this.clearAllFilters();
        });

        // Refresh button
        document.getElementById('refresh-btn').addEventListener('click', async () => {
            await this.refreshLibrary();
        });

        // View toggle
        document.querySelectorAll('.view-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
                e.currentTarget.classList.add('active');
                this.currentView = e.currentTarget.dataset.view;
                this.renderBooks();
            });
        });

        // Sort
        document.getElementById('sort-select').addEventListener('change', (e) => {
            this.currentSort = e.target.value;
            this.applyFilters();
        });

        // Index toggles
        document.querySelectorAll('.index-toggle').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const indexType = e.currentTarget.dataset.index;
                if (indexType === 'all') {
                    this.clearAllFilters();
                    return;
                }

                const itemsList = document.getElementById(`${indexType}-list`);
                itemsList.classList.toggle('expanded');
            });
        });

        // Modal
        document.querySelector('.modal-close').addEventListener('click', () => {
            this.closeModal();
        });

        document.getElementById('book-modal').addEventListener('click', (e) => {
            if (e.target.id === 'book-modal') {
                this.closeModal();
            }
        });
    }

    buildIndices() {
        this.buildIndex('authors', 'author');
        this.buildIndex('genres', 'genre_subcategory');
        this.buildIndex('eras', 'literary_era');
        this.buildIndex('publishers', 'publisher');
        this.buildIndex('narrators', 'narrator');
        this.buildIndex('series', 'series', true); // skip empty
        this.buildTopicsIndex();
    }

    buildIndex(indexType, field, skipEmpty = false) {
        const counts = {};
        this.allBooks.forEach(book => {
            const value = book[field];
            if (skipEmpty && !value) return;
            counts[value] = (counts[value] || 0) + 1;
        });

        const sorted = Object.entries(counts)
            .sort((a, b) => a[0].localeCompare(b[0]));

        const listElement = document.getElementById(`${indexType}-list`);
        listElement.innerHTML = sorted.map(([value, count]) => `
            <div class="index-item" data-index="${indexType}" data-value="${this.escapeHtml(value)}">
                ${this.escapeHtml(value)}
                <span class="item-count">(${count})</span>
            </div>
        `).join('');

        document.getElementById(`${indexType.slice(0, -1)}-count`).textContent = sorted.length;

        // Add click handlers
        listElement.querySelectorAll('.index-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const filterType = e.currentTarget.dataset.index.slice(0, -1); // Remove 's'
                const value = e.currentTarget.dataset.value;
                this.setFilter(filterType, value);
            });
        });
    }

    buildTopicsIndex() {
        const counts = {};
        this.allBooks.forEach(book => {
            book.topics.forEach(topic => {
                counts[topic] = (counts[topic] || 0) + 1;
            });
        });

        const sorted = Object.entries(counts)
            .sort((a, b) => b[1] - a[1]); // Sort by count

        const listElement = document.getElementById('topics-list');
        listElement.innerHTML = sorted.map(([topic, count]) => `
            <div class="index-item" data-index="topics" data-value="${this.escapeHtml(topic)}">
                ${this.escapeHtml(topic)}
                <span class="item-count">(${count})</span>
            </div>
        `).join('');

        document.getElementById('topic-count').textContent = sorted.length;

        listElement.querySelectorAll('.index-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const value = e.currentTarget.dataset.value;
                this.setFilter('topic', value);
            });
        });
    }

    setFilter(type, value) {
        this.activeFilters[type] = value;
        this.currentPage = 1;
        this.applyFilters();
        this.updateActiveFiltersDisplay();
    }

    clearFilter(type) {
        this.activeFilters[type] = null;
        this.applyFilters();
        this.updateActiveFiltersDisplay();
    }

    clearAllFilters() {
        Object.keys(this.activeFilters).forEach(key => {
            this.activeFilters[key] = key === 'search' ? '' : null;
        });
        document.getElementById('search-input').value = '';
        this.currentPage = 1;
        this.applyFilters();
        this.updateActiveFiltersDisplay();

        // Update UI
        document.querySelectorAll('.index-item').forEach(item => {
            item.classList.remove('selected');
        });
    }

    updateActiveFiltersDisplay() {
        const container = document.getElementById('active-filters');
        const filters = [];

        Object.entries(this.activeFilters).forEach(([type, value]) => {
            if (value && value !== '') {
                filters.push(`
                    <div class="filter-tag">
                        <span>${type}: ${this.escapeHtml(value)}</span>
                        <button onclick="library.clearFilter('${type}')">&times;</button>
                    </div>
                `);
            }
        });

        container.innerHTML = filters.join('');

        // Update selected items in sidebar
        document.querySelectorAll('.index-item').forEach(item => {
            item.classList.remove('selected');
            const itemType = item.dataset.index.slice(0, -1);
            const itemValue = item.dataset.value;
            if (this.activeFilters[itemType] === itemValue) {
                item.classList.add('selected');
            }
        });
    }

    applyFilters() {
        this.filteredBooks = this.allBooks.filter(book => {
            // Search filter
            if (this.activeFilters.search) {
                const searchTerm = this.activeFilters.search;
                const searchableText = `${book.title} ${book.author} ${book.narrator} ${book.description}`.toLowerCase();
                if (!searchableText.includes(searchTerm)) return false;
            }

            // Other filters
            if (this.activeFilters.author && book.author !== this.activeFilters.author) return false;
            if (this.activeFilters.genre && book.genre_subcategory !== this.activeFilters.genre) return false;
            if (this.activeFilters.era && book.literary_era !== this.activeFilters.era) return false;
            if (this.activeFilters.publisher && book.publisher !== this.activeFilters.publisher) return false;
            if (this.activeFilters.narrator && book.narrator !== this.activeFilters.narrator) return false;
            if (this.activeFilters.series && book.series !== this.activeFilters.series) return false;
            if (this.activeFilters.topic && !book.topics.includes(this.activeFilters.topic)) return false;

            return true;
        });

        this.sortBooks();
        this.renderBooks();
    }

    sortBooks() {
        this.filteredBooks.sort((a, b) => {
            switch (this.currentSort) {
                case 'title':
                    return a.title.localeCompare(b.title);
                case 'author':
                    return a.author.localeCompare(b.author);
                case 'year':
                    return (b.year || '0').localeCompare(a.year || '0');
                case 'duration':
                    return b.duration_hours - a.duration_hours;
                default:
                    return 0;
            }
        });
    }

    renderBooks() {
        const container = document.getElementById('books-container');
        const start = (this.currentPage - 1) * this.booksPerPage;
        const end = start + this.booksPerPage;
        const booksToShow = this.filteredBooks.slice(start, end);

        // Update container class
        container.className = this.currentView === 'grid' ? 'books-grid' : 'books-list';

        // Update results count
        document.getElementById('showing-count').textContent =
            `Showing ${start + 1}-${Math.min(end, this.filteredBooks.length)} of ${this.filteredBooks.length} audiobooks`;

        // Render books
        if (booksToShow.length === 0) {
            container.innerHTML = '<p style="grid-column: 1/-1; text-align: center; font-size: 1.2em; color: var(--text-secondary);">No audiobooks found matching your criteria.</p>';
        } else {
            container.innerHTML = booksToShow.map(book => this.createBookCard(book)).join('');

            // Add click handlers
            container.querySelectorAll('.book-card').forEach((card, index) => {
                card.addEventListener('click', () => {
                    this.showBookDetail(booksToShow[index]);
                });
            });
        }

        this.renderPagination();
    }

    createBookCard(book) {
        const coverHtml = book.cover
            ? `<img src="${book.cover}" alt="${this.escapeHtml(book.title)}" class="book-cover">`
            : `<div class="book-cover-placeholder">📚</div>`;

        const description = this.currentView === 'list' && book.description
            ? `<p class="book-description">${this.escapeHtml(book.description)}</p>`
            : '';

        // Format and quality info
        const format = book.format ? book.format.toUpperCase() : 'M4B';
        const formatInfo = format ? ` (${format})` : '';

        return `
            <div class="book-card">
                ${coverHtml}
                <div class="book-info">
                    <div class="book-title">${this.escapeHtml(book.title)}${formatInfo}</div>
                    <div class="book-author">${this.escapeHtml(book.author)}</div>
                    ${description}
                    <div class="book-meta">
                        <span>${book.duration_formatted}</span>
                        <span>${book.year || 'N/A'}</span>
                    </div>
                </div>
            </div>
        `;
    }

    renderPagination() {
        const totalPages = Math.ceil(this.filteredBooks.length / this.booksPerPage);
        const pagination = document.getElementById('pagination');

        if (totalPages <= 1) {
            pagination.innerHTML = '';
            return;
        }

        const buttons = [];

        // Previous button
        buttons.push(`
            <button ${this.currentPage === 1 ? 'disabled' : ''} onclick="library.goToPage(${this.currentPage - 1})">
                Previous
            </button>
        `);

        // Page numbers
        const maxButtons = 7;
        let startPage = Math.max(1, this.currentPage - Math.floor(maxButtons / 2));
        let endPage = Math.min(totalPages, startPage + maxButtons - 1);

        if (endPage - startPage < maxButtons - 1) {
            startPage = Math.max(1, endPage - maxButtons + 1);
        }

        if (startPage > 1) {
            buttons.push(`<button onclick="library.goToPage(1)">1</button>`);
            if (startPage > 2) buttons.push(`<button disabled>...</button>`);
        }

        for (let i = startPage; i <= endPage; i++) {
            buttons.push(`
                <button class="${i === this.currentPage ? 'active' : ''}" onclick="library.goToPage(${i})">
                    ${i}
                </button>
            `);
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) buttons.push(`<button disabled>...</button>`);
            buttons.push(`<button onclick="library.goToPage(${totalPages})">${totalPages}</button>`);
        }

        // Next button
        buttons.push(`
            <button ${this.currentPage === totalPages ? 'disabled' : ''} onclick="library.goToPage(${this.currentPage + 1})">
                Next
            </button>
        `);

        pagination.innerHTML = buttons.join('');
    }

    goToPage(page) {
        this.currentPage = page;
        this.renderBooks();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    showBookDetail(book) {
        const modal = document.getElementById('book-modal');
        const modalBody = document.getElementById('modal-body');

        const coverHtml = book.cover
            ? `<img src="${book.cover}" alt="${this.escapeHtml(book.title)}" class="modal-book-cover">`
            : `<div class="modal-book-cover-placeholder">📚</div>`;

        modalBody.innerHTML = `
            <div class="modal-book-detail">
                <div>
                    ${coverHtml}
                </div>
                <div class="modal-book-info">
                    <h2>${this.escapeHtml(book.title)}</h2>
                    <p class="author">by ${this.escapeHtml(book.author)}</p>

                    ${book.description ? `
                        <div class="modal-detail-section">
                            <h3>Description</h3>
                            <p>${this.escapeHtml(book.description)}</p>
                        </div>
                    ` : ''}

                    <div class="modal-detail-section">
                        <h3>Details</h3>
                        <div class="detail-grid">
                            <div class="detail-item">
                                <span class="detail-label">Narrator</span>
                                <span class="detail-value">${this.escapeHtml(book.narrator)}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Publisher</span>
                                <span class="detail-value">${this.escapeHtml(book.publisher)}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Duration</span>
                                <span class="detail-value">${book.duration_formatted}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Published</span>
                                <span class="detail-value">${book.year || 'Unknown'}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Genre</span>
                                <span class="detail-value">${this.escapeHtml(book.genre_subcategory)}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">Era</span>
                                <span class="detail-value">${book.literary_era}</span>
                            </div>
                            ${book.series ? `
                                <div class="detail-item">
                                    <span class="detail-label">Series</span>
                                    <span class="detail-value">${this.escapeHtml(book.series)}${book.series_part ? ` #${book.series_part}` : ''}</span>
                                </div>
                            ` : ''}
                            <div class="detail-item">
                                <span class="detail-label">File Size</span>
                                <span class="detail-value">${book.file_size_mb} MB</span>
                            </div>
                        </div>
                    </div>

                    ${book.topics.length > 0 ? `
                        <div class="modal-detail-section">
                            <h3>Topics</h3>
                            <div class="tag-list">
                                ${book.topics.map(topic => `<span class="tag">${this.escapeHtml(topic)}</span>`).join('')}
                            </div>
                        </div>
                    ` : ''}

                    <div class="modal-detail-section">
                        <h3>File Location</h3>
                        <p style="font-family: monospace; font-size: 0.9em; word-break: break-all;">
                            ${this.escapeHtml(book.file_path)}
                        </p>
                    </div>
                </div>
            </div>
        `;

        modal.classList.add('show');
        document.body.style.overflow = 'hidden';
    }

    closeModal() {
        const modal = document.getElementById('book-modal');
        modal.classList.remove('show');
        document.body.style.overflow = '';
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize library
let library;
document.addEventListener('DOMContentLoaded', () => {
    library = new AudiobookLibrary();
});
