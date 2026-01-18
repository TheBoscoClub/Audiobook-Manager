// Modern Audiobook Library - API-backed with pagination
// Use relative URL for proxy support (works with both direct API and HTTPS proxy)
const API_BASE = '/api';

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

        // Author autocomplete state
        this.authorLetterGroup = 'all';
        this.authorSortAsc = true;
        this.highlightedAuthorIndex = -1;

        // Collections state
        this.collections = [];
        this.currentCollection = '';

        this.init();
    }

    /**
     * Extract sort key from a name - returns "LastName, FirstName" format
     * Handles:
     * - Single name: "First Last" → "Last, First"
     * - Multiple names: "First Last, Second Name, ..." → sort by first person's last name
     * - Anthologies: "Gaiman (contributor), Martin (editor)" → sort by editor (Martin)
     * - Named groups: "Full Cast" → treat entire name as surname
     * - Role suffixes: "Name (editor)", "Name - translator" → strip role, sort by name
     */
    getNameSortKey(name) {
        if (!name) return '';

        // Check for comma-separated multiple names
        if (name.includes(',')) {
            const parts = name.split(',').map(p => p.trim());

            // If there are "(contributor)" entries, this is an anthology - find the editor
            const hasContributors = parts.some(p => /\(contributor\)/i.test(p));
            if (hasContributors) {
                // Find the editor - they're the "author of record" for anthologies
                const editor = parts.find(p => /\(editor\)/i.test(p) || /- editor/i.test(p));
                if (editor) {
                    return this.getNameSortKey(editor);
                }
            }

            // Otherwise use first person in the list
            return this.getNameSortKey(parts[0]);
        }

        // Strip role suffixes: "(editor)", "(translator)", "- editor", etc.
        let cleanName = name
            .replace(/\s*\([^)]*\)\s*/g, '')  // Remove (anything in parentheses)
            .replace(/\s*-\s*(editor|translator|contributor|introduction|author|authoreditor|editorauthor)\s*/gi, '')
            .trim();

        // If stripping left us with nothing, use original
        if (!cleanName) cleanName = name.trim();

        // Known group names - treat entire name as surname (no first name)
        const groupNames = ['full cast', 'various authors', 'various narrators', 'various', 'unknown narrator', 'unknown author'];
        if (groupNames.includes(cleanName.toLowerCase())) {
            return cleanName.toLowerCase();
        }

        const parts = cleanName.split(/\s+/);
        if (parts.length === 1) return cleanName.toLowerCase();

        // Last word is the last name, everything else is first/middle
        const lastName = parts[parts.length - 1];
        const firstName = parts.slice(0, -1).join(' ');
        return `${lastName}, ${firstName}`.toLowerCase();
    }

    /**
     * Sort names by last name, first name
     */
    sortByLastName(names, ascending = true) {
        return names.sort((a, b) => {
            const keyA = this.getNameSortKey(a);
            const keyB = this.getNameSortKey(b);
            const cmp = keyA.localeCompare(keyB, undefined, { sensitivity: 'base' });
            return ascending ? cmp : -cmp;
        });
    }

    async init() {
        await this.loadStats();
        await this.loadFilters();
        await this.loadCollections();
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

            // Load narrator counts for autocomplete
            await this.loadNarratorCounts();

            // Setup author autocomplete (similar to narrator)
            this.setupAuthorAutocomplete();

            // Setup narrator autocomplete
            this.setupNarratorAutocomplete();
        } catch (error) {
            console.error('Error loading filters:', error);
        }
    }

    async loadCollections() {
        try {
            const response = await fetch(`${API_BASE}/collections`);
            this.collections = await response.json();
            this.renderCollectionButtons();
        } catch (error) {
            console.error('Error loading collections:', error);
        }
    }

    renderCollectionButtons() {
        const container = document.getElementById('collections-buttons');
        if (!container || this.collections.length === 0) {
            return;
        }

        // Group collections by category
        const grouped = {};
        this.collections.forEach(c => {
            const cat = c.category || 'main';
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push(c);
        });

        // Render grouped buttons for sidebar
        let html = '';
        const categoryOrder = ['special', 'main', 'nonfiction', 'subgenre'];
        const categoryLabels = {
            special: 'Special Collections',
            main: 'Fiction Genres',
            nonfiction: 'Nonfiction',
            subgenre: 'More Genres'
        };

        categoryOrder.forEach(cat => {
            if (!grouped[cat] || grouped[cat].length === 0) return;

            html += `<div class="collection-category">`;
            html += `<span class="collection-category-label">${categoryLabels[cat]}</span>`;
            html += `<div class="collection-category-items">`;

            grouped[cat].forEach(collection => {
                html += `
                    <button class="collection-btn ${this.currentCollection === collection.id ? 'active' : ''}"
                            data-collection="${collection.id}"
                            title="${collection.description}">
                        <span class="icon">${collection.icon}</span>
                        <span class="name">${collection.name}</span>
                        <span class="count">${collection.count}</span>
                    </button>
                `;
            });

            html += `</div></div>`;
        });

        container.innerHTML = html;

        // Add click handlers
        container.querySelectorAll('.collection-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const collectionId = btn.dataset.collection;
                this.toggleCollection(collectionId);
            });
        });

        // Update active filter badge
        this.updateFilterBadge();
    }

    updateFilterBadge() {
        const badge = document.getElementById('active-filter-badge');
        if (!badge) return;

        if (this.currentCollection) {
            const collection = this.collections.find(c => c.id === this.currentCollection);
            if (collection) {
                badge.textContent = collection.icon + ' ' + collection.name;
                badge.classList.add('visible');
            }
        } else {
            badge.textContent = '';
            badge.classList.remove('visible');
        }
    }

    toggleCollection(collectionId) {
        if (this.currentCollection === collectionId) {
            // Deselect - show all books
            this.currentCollection = '';
        } else {
            // Select this collection
            this.currentCollection = collectionId;
        }
        this.currentPage = 1;
        this.renderCollectionButtons();
        this.loadAudiobooks();
        // Close sidebar after selection on mobile
        if (window.innerWidth < 768) {
            this.closeSidebar();
        }
    }

    openSidebar() {
        const sidebar = document.getElementById('collections-sidebar');
        const overlay = document.getElementById('sidebar-overlay');
        if (sidebar) sidebar.classList.add('open');
        if (overlay) overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    closeSidebar() {
        const sidebar = document.getElementById('collections-sidebar');
        const overlay = document.getElementById('sidebar-overlay');
        if (sidebar) sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    setupSidebarEvents() {
        // Toggle button
        const toggleBtn = document.getElementById('sidebar-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => this.openSidebar());
        }

        // Close button
        const closeBtn = document.getElementById('sidebar-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closeSidebar());
        }

        // Overlay click to close
        const overlay = document.getElementById('sidebar-overlay');
        if (overlay) {
            overlay.addEventListener('click', () => this.closeSidebar());
        }

        // Clear filter button
        const clearBtn = document.getElementById('sidebar-clear-filter');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                this.currentCollection = '';
                this.currentPage = 1;
                this.renderCollectionButtons();
                this.loadAudiobooks();
            });
        }

        // Escape key to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeSidebar();
            }
        });
    }

    setupAuthorAutocomplete() {
        const container = document.getElementById('author-autocomplete');
        const input = document.getElementById('author-search');
        const dropdown = document.getElementById('author-dropdown');
        const clearBtn = document.getElementById('author-clear');
        const sortBtn = document.getElementById('author-sort');

        if (!container || !input || !dropdown) return;

        // Letter group buttons
        container.querySelectorAll('.letter-group').forEach(btn => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.letter-group').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.authorLetterGroup = btn.dataset.group;
                const query = input.value.toLowerCase().trim();
                this.highlightedAuthorIndex = -1;
                this.showAuthorDropdown(query);
            });
        });

        // Sort toggle button
        if (sortBtn) {
            sortBtn.addEventListener('click', () => {
                this.authorSortAsc = !this.authorSortAsc;
                sortBtn.textContent = this.authorSortAsc ? 'A-Z' : 'Z-A';
                const query = input.value.toLowerCase().trim();
                this.highlightedAuthorIndex = -1;
                this.showAuthorDropdown(query);
            });
        }

        // Input event - filter authors as user types
        input.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase().trim();
            this.highlightedAuthorIndex = -1;
            this.showAuthorDropdown(query);
        });

        // Focus event - show dropdown
        input.addEventListener('focus', () => {
            const query = input.value.toLowerCase().trim();
            this.showAuthorDropdown(query);
        });

        // Keyboard navigation
        input.addEventListener('keydown', (e) => {
            const options = dropdown.querySelectorAll('.author-option');

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.highlightedAuthorIndex = Math.min(this.highlightedAuthorIndex + 1, options.length - 1);
                this.updateAuthorHighlight(options);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this.highlightedAuthorIndex = Math.max(this.highlightedAuthorIndex - 1, -1);
                this.updateAuthorHighlight(options);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (this.highlightedAuthorIndex >= 0 && options[this.highlightedAuthorIndex]) {
                    options[this.highlightedAuthorIndex].click();
                }
            } else if (e.key === 'Escape') {
                this.hideAuthorDropdown();
                input.blur();
            }
        });

        // Clear button
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                this.selectAuthor('');
            });
        }

        // Click outside to close
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) {
                this.hideAuthorDropdown();
            }
        });
    }

    showAuthorDropdown(query = '') {
        const dropdown = document.getElementById('author-dropdown');
        const authors = this.filters.authors || [];

        // Filter by letter group first
        let filtered = this.filterByLetterGroup(authors, this.authorLetterGroup);

        // Then filter by search query
        if (query) {
            filtered = filtered.filter(a => a.toLowerCase().includes(query));
        }

        // Sort the results
        filtered = this.sortByLastName(filtered, this.authorSortAsc);

        // Count for this group before limiting
        const groupTotal = filtered.length;

        // Limit display to prevent performance issues
        const maxDisplay = 50;
        const hasMore = filtered.length > maxDisplay;
        filtered = filtered.slice(0, maxDisplay);

        // Build dropdown HTML
        let html = '';

        // "All Authors" option at top
        const allLabel = this.authorLetterGroup === 'all' ? 'All Authors' : `All ${this.authorLetterGroup.toUpperCase()}`;
        html += `<div class="author-option author-all-option" data-value="">
            <span>${allLabel}</span>
            <span class="count">${authors.length} total</span>
        </div>`;

        if (filtered.length === 0 && query) {
            html += `<div class="author-no-results">No authors matching "${query}"</div>`;
        } else if (filtered.length === 0) {
            html += `<div class="author-no-results">No authors in this range</div>`;
        } else {
            filtered.forEach(author => {
                html += `<div class="author-option" data-value="${this.escapeHtml(author)}">
                    <span>${this.highlightMatch(author, query)}</span>
                </div>`;
            });

            if (hasMore) {
                html += `<div class="author-no-results">Showing ${maxDisplay} of ${groupTotal}. Type to filter...</div>`;
            }
        }

        dropdown.innerHTML = html;

        // Add click handlers to options
        dropdown.querySelectorAll('.author-option').forEach(option => {
            option.addEventListener('click', () => {
                this.selectAuthor(option.dataset.value);
            });
        });

        dropdown.classList.add('active');
    }

    hideAuthorDropdown() {
        const dropdown = document.getElementById('author-dropdown');
        if (dropdown) {
            dropdown.classList.remove('active');
        }
    }

    updateAuthorHighlight(options) {
        options.forEach((opt, idx) => {
            opt.classList.toggle('highlighted', idx === this.highlightedAuthorIndex);
        });

        // Scroll into view
        if (this.highlightedAuthorIndex >= 0 && options[this.highlightedAuthorIndex]) {
            options[this.highlightedAuthorIndex].scrollIntoView({ block: 'nearest' });
        }
    }

    selectAuthor(author) {
        const input = document.getElementById('author-search');
        const clearBtn = document.getElementById('author-clear');

        this.currentFilters.author = author;
        input.value = author;

        // Show/hide clear button
        if (clearBtn) {
            clearBtn.style.display = author ? 'block' : 'none';
        }

        this.hideAuthorDropdown();
        this.currentPage = 1;
        this.loadAudiobooks();
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
                const countHtml = (count != null) ? `<span class="count">${count}</span>` : '';
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

    /**
     * Get the last name from a single person's name (no commas)
     */
    extractLastName(singleName) {
        // Strip role suffixes first
        let clean = singleName
            .replace(/\s*\([^)]*\)\s*/g, '')
            .replace(/\s*-\s*(editor|translator|contributor|introduction|author|authoreditor|editorauthor)\s*/gi, '')
            .trim();
        if (!clean) clean = singleName.trim();

        const parts = clean.split(/\s+/);
        return parts.length > 1 ? parts[parts.length - 1] : clean;
    }

    filterByLetterGroup(names, group) {
        if (group === 'all') return [...names];

        const ranges = {
            'a-e': ['A', 'B', 'C', 'D', 'E'],
            'f-j': ['F', 'G', 'H', 'I', 'J'],
            'k-o': ['K', 'L', 'M', 'N', 'O'],
            'p-t': ['P', 'Q', 'R', 'S', 'T'],
            'u-z': ['U', 'V', 'W', 'X', 'Y', 'Z']
        };

        const letters = ranges[group] || [];
        return names.filter(name => {
            // For co-authored works, check if ANY author's last name matches
            // This allows "Stephen King, Peter Straub" to appear under both K and S
            if (name.includes(',')) {
                const people = name.split(',').map(p => p.trim());
                // Skip if it's an anthology (has contributors) - use editor only
                const hasContributors = people.some(p => /\(contributor\)/i.test(p));
                if (hasContributors) {
                    const editor = people.find(p => /\(editor\)/i.test(p) || /- editor/i.test(p));
                    if (editor) {
                        const lastName = this.extractLastName(editor);
                        return letters.includes(lastName.charAt(0).toUpperCase());
                    }
                }
                // Co-authored: match if ANY author's last name is in the letter group
                return people.some(person => {
                    const lastName = this.extractLastName(person);
                    return letters.includes(lastName.charAt(0).toUpperCase());
                });
            }

            // Single author - use sort key
            const sortKey = this.getNameSortKey(name);
            const firstLetter = sortKey.charAt(0).toUpperCase();
            return letters.includes(firstLetter);
        });
    }

    sortNarrators(narrators, ascending) {
        return this.sortByLastName(narrators, ascending);
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
            if (this.currentCollection) params.append('collection', this.currentCollection);
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
    }

    createBookCard(book) {
        const formatQuality = book.format ? book.format.toUpperCase() : 'M4B';
        const quality = book.quality ? ` ${book.quality}` : '';
        const hasSupplement = book.supplement_count > 0;
        const hasEditions = book.edition_count && book.edition_count > 1;

        // Check for saved playback position
        const savedPosition = playbackManager ? playbackManager.getPosition(book.id) : null;
        const percentComplete = savedPosition ? playbackManager.getPercentComplete(book.id) : 0;
        const hasContinue = percentComplete > 0;

        return `
            <div class="book-card" data-id="${book.id}">
                <div class="book-cover">
                    ${book.cover_path ?
                `<img src="/covers/${book.cover_path}" alt="${this.escapeHtml(book.title)}" onerror="this.parentElement.innerHTML='<span class=\\'book-cover-placeholder\\'>📖</span>'">` :
                '<span class="book-cover-placeholder">📖</span>'
            }
                    ${hasSupplement ? `<span class="supplement-badge" title="Has PDF supplement" onclick="event.stopPropagation(); library.showSupplements(${book.id})">PDF</span>` : ''}
                    ${hasContinue ? `<span class="continue-badge" title="${percentComplete}% complete">Continue</span>` : ''}
                    ${hasEditions ? `<span class="editions-badge" title="${book.edition_count} editions" onclick="event.stopPropagation(); library.toggleEditions(${book.id})">${book.edition_count} editions</span>` : ''}
                </div>
                <div class="book-title">${this.escapeHtml(book.title)}</div>
                ${book.author ? `<div class="book-author">by ${this.escapeHtml(book.author)}</div>` : ''}
                ${book.narrator ? `<div class="book-narrator">Narrated by ${this.escapeHtml(book.narrator)}</div>` : ''}
                <div class="book-meta">
                    <span class="book-format">${formatQuality}${quality}</span>
                    <span class="book-duration">${book.duration_formatted || `${Math.round(book.duration_hours || 0)}h`}</span>
                </div>
                ${hasContinue ? `
                <div class="book-progress">
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: ${percentComplete}%"></div>
                    </div>
                    <span class="progress-text">${percentComplete}%</span>
                </div>
                ` : ''}
                <div class="book-actions">
                    <button class="btn-play" onclick="event.stopPropagation(); audioPlayer.playAudiobook(${JSON.stringify(book).replace(/"/g, '&quot;')}, false)">▶ Play</button>
                    <button class="btn-resume" ${!hasContinue ? 'disabled' : ''} onclick="event.stopPropagation(); audioPlayer.playAudiobook(${JSON.stringify(book).replace(/"/g, '&quot;')}, true)" title="${hasContinue ? 'Resume from ' + playbackManager.formatTime(savedPosition.position) : 'No saved position'}">
                        ${hasContinue ? '⏯ Resume' : '⏯ Resume'}
                    </button>
                </div>
                ${hasEditions ? '<div class="book-editions" data-book-id="' + book.id + '" style="display: none;"></div>' : ''}
            </div>
        `;
    }

    async toggleEditions(bookId) {
        const editionsContainer = document.querySelector(`.book-editions[data-book-id="${bookId}"]`);
        if (!editionsContainer) return;

        // Toggle visibility
        const isVisible = editionsContainer.style.display !== 'none';

        if (isVisible) {
            // Hide editions
            editionsContainer.style.display = 'none';
        } else {
            // Show editions - fetch if not already loaded
            if (!editionsContainer.dataset.loaded) {
                try {
                    const response = await fetch(`${API_BASE}/audiobooks/${bookId}/editions`);
                    const data = await response.json();

                    if (data.editions && data.editions.length > 0) {
                        editionsContainer.innerHTML = this.renderEditions(data.editions);
                        editionsContainer.dataset.loaded = 'true';
                    } else {
                        editionsContainer.innerHTML = '<p style="padding: 1rem; text-align: center;">No other editions found.</p>';
                    }
                } catch (error) {
                    console.error('Error loading editions:', error);
                    editionsContainer.innerHTML = '<p style="padding: 1rem; color: #c0392b;">Error loading editions.</p>';
                }
            }
            editionsContainer.style.display = 'block';
        }
    }

    renderEditions(editions) {
        return `
            <div class="editions-header">Available Editions</div>
            <div class="editions-list">
                ${editions.map(edition => this.renderEditionItem(edition)).join('')}
            </div>
        `;
    }

    renderEditionItem(edition) {
        const formatQuality = edition.format ? edition.format.toUpperCase() : 'M4B';
        const quality = edition.quality ? ` ${edition.quality}` : '';
        const savedPosition = playbackManager ? playbackManager.getPosition(edition.id) : null;
        const percentComplete = savedPosition ? playbackManager.getPercentComplete(edition.id) : 0;
        const hasContinue = percentComplete > 0;

        return `
            <div class="edition-item">
                <div class="edition-info">
                    <div class="edition-narrator">🎙️ ${this.escapeHtml(edition.narrator || 'Unknown Narrator')}</div>
                    <div class="edition-details">
                        <span class="edition-format">${formatQuality}${quality}</span>
                        <span class="edition-duration">${edition.duration_formatted || `${Math.round(edition.duration_hours || 0)}h`}</span>
                        <span class="edition-size">${Math.round(edition.file_size_mb)}MB</span>
                        ${hasContinue ? `<span class="edition-progress">${percentComplete}% played</span>` : ''}
                    </div>
                </div>
                <div class="edition-actions">
                    <button class="btn-play-edition" onclick="event.stopPropagation(); audioPlayer.playAudiobook(${JSON.stringify(edition).replace(/"/g, '&quot;')}, false)">▶ Play</button>
                    ${hasContinue ? `<button class="btn-resume-edition" onclick="event.stopPropagation(); audioPlayer.playAudiobook(${JSON.stringify(edition).replace(/"/g, '&quot;')}, true)">⏯ Resume</button>` : ''}
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
            // Clear author autocomplete
            this.selectAuthor('');
            // Clear narrator autocomplete
            this.selectNarrator('');
            // Clear collection filter
            this.currentCollection = '';
            this.renderCollectionButtons();
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

        // Setup sidebar events
        this.setupSidebarEvents();
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
        this.playbackRates = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5];
        this.currentRateIndex = 2; // Start at 1.0x
        this.saveTimeout = null; // For debouncing position saves

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
            // Clear saved position when audiobook finishes
            if (this.currentBook && playbackManager) {
                playbackManager.clearPosition(this.currentBook.id);
            }
        });

        this.audio.addEventListener('play', () => {
            document.getElementById('play-pause').textContent = '⏸';
        });

        this.audio.addEventListener('pause', () => {
            document.getElementById('play-pause').textContent = '▶';
        });
    }

    async playAudiobook(book, resume = false) {
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
        this.audio.src = `${API_BASE}/stream/` + book.id;

        // Load saved playback speed
        if (playbackManager) {
            const savedSpeed = playbackManager.getSpeed();
            const speedIndex = this.playbackRates.indexOf(savedSpeed);
            if (speedIndex !== -1) {
                this.currentRateIndex = speedIndex;
            }
        }
        this.audio.playbackRate = this.playbackRates[this.currentRateIndex];
        document.getElementById('playback-speed').textContent = this.playbackRates[this.currentRateIndex] + 'x';

        // Show player
        this.player.style.display = 'block';

        // Handle resume - check both localStorage and API for best position
        if (resume && playbackManager) {
            // Use async getBestPosition to check both local and API (furthest ahead wins)
            playbackManager.getBestPosition(book.id).then(savedPosition => {
                if (savedPosition && savedPosition.position > 30) {
                    // Wait for metadata to load, then seek
                    const seekHandler = () => {
                        this.audio.currentTime = savedPosition.position;
                        console.log(`Resumed at ${savedPosition.position}s from ${savedPosition.source || 'local'}`);
                    };

                    if (this.audio.readyState >= 1) {
                        // Metadata already loaded
                        seekHandler();
                    } else {
                        this.audio.addEventListener('loadedmetadata', seekHandler, { once: true });
                    }
                }
            });
        }

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

        // Save speed preference
        if (playbackManager) {
            playbackManager.saveSpeed(newRate);
        }
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

        // Auto-save position (debounced to every 5 seconds)
        if (this.currentBook && playbackManager && this.audio.currentTime > 0) {
            if (this.saveTimeout) {
                clearTimeout(this.saveTimeout);
            }
            this.saveTimeout = setTimeout(() => {
                playbackManager.savePosition(
                    this.currentBook.id,
                    this.audio.currentTime,
                    this.audio.duration
                );
            }, 5000);
        }
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
        // Save position before closing (both localStorage and API)
        if (this.currentBook && playbackManager && this.audio.currentTime > 0 && this.audio.duration) {
            playbackManager.savePosition(
                this.currentBook.id,
                this.audio.currentTime,
                this.audio.duration
            );
            // Flush to API immediately on close (don't wait for debounce)
            playbackManager.flushToAPI(this.currentBook.id, this.audio.currentTime);
        }

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
            // Use safe DOM methods to avoid XSS - create element and set textContent
            const errorP = document.createElement('p');
            errorP.style.color = '#c0392b';
            errorP.textContent = `Error loading statistics: ${error.message}`;
            content.innerHTML = '';
            content.appendChild(errorP);
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
            // Use safe DOM methods to avoid XSS - error.message could contain malicious content
            content.innerHTML = '';
            const errorP = document.createElement('p');
            errorP.style.color = '#c0392b';
            errorP.textContent = `Error loading duplicates: ${error.message}`;
            content.appendChild(errorP);

            // Add static help text (safe - no user input)
            if (currentMode === 'hash') {
                const helpP = document.createElement('p');
                helpP.textContent = 'Make sure hashes have been generated first:';
                content.appendChild(helpP);
                const pre = document.createElement('pre');
                pre.className = 'cli-command';
                pre.textContent = 'cd library && python3 scripts/generate_hashes.py';
                content.appendChild(pre);
            }
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

// Playback Manager - handles playback position persistence
// Dual-layer storage: localStorage (fast cache) + API (persistent/syncable)
class PlaybackManager {
    constructor() {
        this.storagePrefix = 'audiobook_';
        this.saveInterval = null;
        this.apiSaveTimeout = null;  // Separate debounce for API saves
        this.apiSaveDelay = 15000;   // Save to API every 15 seconds (less frequent than localStorage)
    }

    // Save to localStorage (instant, local cache)
    savePosition(fileId, position, duration) {
        const data = {
            position: position,
            duration: duration,
            timestamp: Date.now()
        };
        localStorage.setItem(`${this.storagePrefix}position_${fileId}`, JSON.stringify(data));

        // Also queue API save (debounced)
        this.queueAPISave(fileId, position);
    }

    // Queue position save to backend API (debounced)
    queueAPISave(fileId, positionSeconds) {
        if (this.apiSaveTimeout) {
            clearTimeout(this.apiSaveTimeout);
        }
        this.apiSaveTimeout = setTimeout(() => {
            this.savePositionToAPI(fileId, Math.floor(positionSeconds * 1000));
        }, this.apiSaveDelay);
    }

    // Save position to backend API
    async savePositionToAPI(fileId, positionMs) {
        try {
            const response = await fetch(`${API_BASE}/position/${fileId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ position_ms: positionMs })
            });
            if (!response.ok) {
                console.warn(`Failed to save position to API: ${response.status}`);
            }
        } catch (error) {
            console.warn('Error saving position to API:', error);
        }
    }

    // Fetch position from backend API
    async getPositionFromAPI(fileId) {
        try {
            const response = await fetch(`${API_BASE}/position/${fileId}`);
            if (response.ok) {
                const data = await response.json();
                if (data.local_position_ms > 0) {
                    return {
                        position: data.local_position_ms / 1000,  // Convert to seconds
                        duration: data.duration_ms ? data.duration_ms / 1000 : 0,
                        timestamp: data.local_position_updated ? new Date(data.local_position_updated).getTime() : 0,
                        source: 'api'
                    };
                }
            }
        } catch (error) {
            console.warn('Error fetching position from API:', error);
        }
        return null;
    }

    // Get best position from both localStorage and API (furthest ahead wins)
    async getBestPosition(fileId) {
        const localPosition = this.getPosition(fileId);
        const apiPosition = await this.getPositionFromAPI(fileId);

        // If neither has data, return null
        if (!localPosition && !apiPosition) return null;

        // If only one has data, use that
        if (!localPosition) return apiPosition;
        if (!apiPosition) return localPosition;

        // Both have data - use furthest ahead (same logic as Audible sync)
        if (apiPosition.position > localPosition.position) {
            console.log(`Using API position (${apiPosition.position}s) over local (${localPosition.position}s)`);
            return apiPosition;
        } else {
            console.log(`Using local position (${localPosition.position}s) over API (${apiPosition.position}s)`);
            return localPosition;
        }
    }

    // Force immediate API save (for player close)
    async flushToAPI(fileId, positionSeconds) {
        if (this.apiSaveTimeout) {
            clearTimeout(this.apiSaveTimeout);
            this.apiSaveTimeout = null;
        }
        await this.savePositionToAPI(fileId, Math.floor(positionSeconds * 1000));
    }

    getPosition(fileId) {
        const data = localStorage.getItem(`${this.storagePrefix}position_${fileId}`);
        if (!data) return null;

        try {
            const parsed = JSON.parse(data);
            // Return null if position is near end (>95%) or very beginning (<30s)
            const percentComplete = (parsed.position / parsed.duration) * 100;
            if (percentComplete > 95 || parsed.position < 30) {
                return null;
            }
            return parsed;
        } catch (e) {
            return null;
        }
    }

    saveSpeed(speed) {
        localStorage.setItem(`${this.storagePrefix}speed`, speed.toString());
    }

    getSpeed() {
        const speed = localStorage.getItem(`${this.storagePrefix}speed`);
        return speed ? parseFloat(speed) : 1.0;
    }

    clearPosition(fileId) {
        localStorage.removeItem(`${this.storagePrefix}position_${fileId}`);
        // Also clear from API
        this.savePositionToAPI(fileId, 0);
    }

    // Format time for display
    formatTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }
        return `${minutes}:${secs.toString().padStart(2, '0')}`;
    }

    getPercentComplete(fileId) {
        const data = this.getPosition(fileId);
        if (!data || !data.duration) return 0;
        return Math.round((data.position / data.duration) * 100);
    }
}

// Initialize managers
let duplicateManager;
let playbackManager;
document.addEventListener('DOMContentLoaded', () => {
    duplicateManager = new DuplicateManager();
    playbackManager = new PlaybackManager();
});
