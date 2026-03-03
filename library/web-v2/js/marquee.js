/**
 * New Books Marquee - Art Deco neon ticker for new audiobook announcements.
 * Fetches new books from /api/user/new-books and displays scrolling titles.
 * Uses safe DOM construction (createElement + textContent only).
 */

/**
 * Initialize the new books marquee.
 * Fetches new books from the API and builds a scrolling ticker if any exist.
 */
function initMarquee() {
    var container = document.getElementById('new-books-marquee');
    if (!container) {
        return;
    }

    fetch('/api/user/new-books', {
        credentials: 'include'
    })
    .then(function(response) {
        if (!response.ok) {
            return null;
        }
        return response.json();
    })
    .then(function(data) {
        if (!data || !data.books || data.books.length === 0) {
            return;
        }

        buildMarquee(container, data.books);
    })
    .catch(function(err) {
        console.log('Marquee: could not load new books:', err.message);
    });
}

/**
 * Build one cycle of marquee content: NEW label + titles + separators.
 * @param {Array} books - Array of book objects with title property.
 * @returns {HTMLElement} A span wrapping one complete cycle.
 */
function buildCycle(books) {
    var cycle = document.createElement('span');
    cycle.className = 'marquee-cycle';

    var label = document.createElement('span');
    label.className = 'marquee-label';
    label.textContent = 'NEW';
    cycle.appendChild(label);

    for (var i = 0; i < books.length; i++) {
        var item = document.createElement('span');
        item.className = 'marquee-item';
        item.textContent = books[i].title || 'Untitled';
        cycle.appendChild(item);

        var sep = document.createElement('span');
        sep.className = 'marquee-separator';
        sep.textContent = '\u2605'; // star character
        cycle.appendChild(sep);
    }
    return cycle;
}

/**
 * Build the marquee DOM structure with book titles.
 * Repeats content enough times to always overflow the viewport,
 * so the seamless scroll loop never shows both copies at once.
 * @param {HTMLElement} container - The marquee container element.
 * @param {Array} books - Array of book objects with title property.
 */
function buildMarquee(container, books) {
    // Clear any existing content safely
    while (container.firstChild) {
        container.removeChild(container.firstChild);
    }

    // Build the scrolling track
    var track = document.createElement('div');
    track.className = 'marquee-track';

    // Insert first cycle and measure its width vs container
    var firstCycle = buildCycle(books);
    track.appendChild(firstCycle);
    container.appendChild(track);

    // Briefly show for measurement (no repaint until JS yields)
    container.classList.remove('hidden');
    var cycleWidth = firstCycle.offsetWidth;
    var containerWidth = container.offsetWidth;

    // Repeat enough times so one full cycle is always off-screen
    var copies = Math.max(2, Math.ceil(containerWidth / Math.max(1, cycleWidth)) + 1);
    copies = Math.min(copies, 20);
    for (var c = 1; c < copies; c++) {
        track.appendChild(buildCycle(books));
    }

    // Dynamic keyframe sized to scroll by exactly one cycle
    var shiftPercent = (100 / copies).toFixed(4);
    var styleEl = document.createElement('style');
    styleEl.textContent =
        '@keyframes marquee-scroll-fill{' +
        '0%{transform:translateX(0)}' +
        '100%{transform:translateX(-' + shiftPercent + '%)}' +
        '}';
    container.appendChild(styleEl);

    // Duration scales with content length
    var duration = Math.max(20, books.length * 5);
    track.style.animation = 'marquee-scroll-fill ' + duration + 's linear infinite';

    // Dismiss button
    var dismissBtn = document.createElement('button');
    dismissBtn.className = 'marquee-dismiss';
    dismissBtn.setAttribute('title', 'Dismiss new books notification');
    dismissBtn.textContent = '\u00D7'; // multiplication sign (x)
    dismissBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        dismissMarquee(container);
    });
    container.appendChild(dismissBtn);
}

/**
 * Dismiss the marquee and notify the server.
 * @param {HTMLElement} container - The marquee container element.
 */
function dismissMarquee(container) {
    container.classList.add('hidden');

    fetch('/api/user/new-books/dismiss', {
        method: 'POST',
        credentials: 'include'
    }).catch(function(err) {
        console.log('Marquee: dismiss failed:', err.message);
    });
}
