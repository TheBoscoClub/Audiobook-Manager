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
        if (!data || !data.books || data.count === 0) {
            return;
        }

        buildMarquee(container, data.books);
    })
    .catch(function(err) {
        console.log('Marquee: could not load new books:', err.message);
    });
}

/**
 * Build the marquee DOM structure with book titles.
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

    // Create a set of titles with separators
    var fragment = document.createDocumentFragment();

    // "NEW" label at the start of each cycle
    var label = document.createElement('span');
    label.className = 'marquee-label';
    label.textContent = 'NEW';
    fragment.appendChild(label);

    for (var i = 0; i < books.length; i++) {
        var item = document.createElement('span');
        item.className = 'marquee-item';
        item.textContent = books[i].title || 'Untitled';
        fragment.appendChild(item);

        // Add separator after each item (including last, for seamless loop)
        var sep = document.createElement('span');
        sep.className = 'marquee-separator';
        sep.textContent = '\u2605'; // star character
        fragment.appendChild(sep);
    }

    track.appendChild(fragment);

    // Duplicate the content for seamless infinite scroll
    var clone = document.createDocumentFragment();

    var cloneLabel = document.createElement('span');
    cloneLabel.className = 'marquee-label';
    cloneLabel.textContent = 'NEW';
    clone.appendChild(cloneLabel);

    for (var j = 0; j < books.length; j++) {
        var cloneItem = document.createElement('span');
        cloneItem.className = 'marquee-item';
        cloneItem.textContent = books[j].title || 'Untitled';
        clone.appendChild(cloneItem);

        var cloneSep = document.createElement('span');
        cloneSep.className = 'marquee-separator';
        cloneSep.textContent = '\u2605';
        clone.appendChild(cloneSep);
    }

    track.appendChild(clone);

    // Set animation duration based on number of titles
    var duration = Math.max(20, books.length * 5);
    track.style.animationDuration = duration + 's';

    container.appendChild(track);

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

    // Clicking the marquee itself also dismisses
    container.addEventListener('click', function() {
        dismissMarquee(container);
    });

    // Show the marquee
    container.classList.remove('hidden');
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
