/**
 * New Books Marquee - Art Deco neon ticker for new audiobook announcements.
 * Fetches new books from /api/user/new-books and displays scrolling titles.
 * Uses safe DOM construction (createElement + textContent only).
 *
 * Two scroll modes based on content width vs viewport:
 *   Ticker:  content < viewport — single copy, scrolls right-to-left like
 *            a news ticker (no visible duplication).
 *   Classic: content >= viewport — seamless 2-copy infinite scroll.
 */

/**
 * Initialize the new books marquee.
 * Fetches new books from the API and builds a scrolling ticker if any exist.
 */
function initMarquee() {
  var container = document.getElementById("new-books-marquee");
  if (!container) {
    return;
  }

  fetch("/api/user/new-books", {
    credentials: "include",
  })
    .then(function (response) {
      if (!response.ok) {
        return null;
      }
      return response.json();
    })
    .then(function (data) {
      if (!data || !data.books || data.books.length === 0) {
        return;
      }

      // Apply translated titles if locale is non-English
      var locale = typeof i18n !== "undefined" ? i18n.getLocale() : "en";
      if (locale !== "en") {
        var ids = data.books.map(function (b) { return b.id; }).join(",");
        fetch("/api/translations/by-locale/" + encodeURIComponent(locale) + "?ids=" + ids, {
          credentials: "include",
        })
          .then(function (r) { return r.ok ? r.json() : {}; })
          .then(function (translations) {
            // Store originals and overlay translations
            data.books.forEach(function (book) {
              var tr = translations[String(book.id)];
              if (tr && tr.title) {
                book._originalTitle = book.title;
                book.title = tr.title;
              }
            });
            buildMarquee(container, data.books);
          })
          .catch(function () {
            buildMarquee(container, data.books);
          });
      } else {
        // Restore originals if switching back to English
        data.books.forEach(function (book) {
          if (book._originalTitle) {
            book.title = book._originalTitle;
            delete book._originalTitle;
          }
        });
        buildMarquee(container, data.books);
      }
    })
    .catch(function (err) {
      console.warn("Marquee: could not load new books:", err.message);
    });
}

/**
 * Build one cycle of marquee content: NEW label + titles + separators.
 * @param {Array} books - Array of book objects with title property.
 * @returns {HTMLElement} A span wrapping one complete cycle.
 */
function buildCycle(books) {
  var cycle = document.createElement("span");
  cycle.className = "marquee-cycle";

  var label = document.createElement("span");
  label.className = "marquee-label";
  label.textContent = typeof t === "function" ? t("marquee.new") : "NEW";
  cycle.appendChild(label);

  for (var i = 0; i < books.length; i++) {
    var fallback = books[i].title || (typeof t === "function" ? t("book.unknownTitle") : "Untitled");
    var item = document.createElement("span");
    item.className = "marquee-item marquee-item-clickable";
    item.textContent = fallback;
    item.dataset.bookId = books[i].id;
    item.title = (typeof t === "function" ? t("marquee.playTitle", { title: fallback }) : "Play " + fallback);
    (function (book) {
      item.addEventListener("click", function (e) {
        e.stopPropagation();
        if (typeof shellPlay === "function") {
          shellPlay(book, false);
        } else if (typeof library !== "undefined" && library.showBookDetail) {
          library.showBookDetail(book.id);
        }
      });
    })(books[i]);
    cycle.appendChild(item);

    var sep = document.createElement("span");
    sep.className = "marquee-separator";
    sep.textContent = "\u2605"; // star character
    cycle.appendChild(sep);
  }
  return cycle;
}

/**
 * Build the marquee DOM structure with book titles.
 *
 * When content is shorter than the viewport (few books), uses ticker mode:
 * a single copy scrolls from right edge to left edge — no visible
 * duplication. When content fills the viewport, uses classic 2-copy
 * infinite scroll for a seamless loop.
 *
 * @param {HTMLElement} container - The marquee container element.
 * @param {Array} books - Array of book objects with title property.
 */
function buildMarquee(container, books) {
  // Clear any existing content safely
  while (container.firstChild) {
    container.removeChild(container.firstChild);
  }

  // Build the scrolling track
  var track = document.createElement("div");
  track.className = "marquee-track";

  // Insert first cycle and measure its width vs container
  var firstCycle = buildCycle(books);
  track.appendChild(firstCycle);
  container.appendChild(track);

  // Show for measurement
  container.classList.remove("hidden");
  var cycleWidth = firstCycle.offsetWidth;
  var containerWidth = container.offsetWidth;

  if (cycleWidth < containerWidth) {
    // TICKER MODE — content shorter than viewport.
    // Single copy scrolls from off-screen right to off-screen left,
    // like a 1930s news ticker. No visible duplication.
    var styleEl = document.createElement("style");
    styleEl.textContent =
      "@keyframes marquee-ticker{" +
      "0%{transform:translateX(" +
      containerWidth +
      "px)}" +
      "100%{transform:translateX(-" +
      cycleWidth +
      "px)}" +
      "}";
    container.appendChild(styleEl);

    // ~80px/s feels natural for a ticker
    var tickerDuration = (containerWidth + cycleWidth) / 80;
    track.style.animation =
      "marquee-ticker " +
      Math.max(8, tickerDuration).toFixed(1) +
      "s linear infinite";
  } else {
    // CLASSIC MODE — content fills or overflows viewport.
    // Duplicate once for seamless infinite scroll (translateX -50%).
    track.appendChild(buildCycle(books));
    var duration = Math.max(20, books.length * 5);
    track.style.animation = "marquee-scroll " + duration + "s linear infinite";
  }

  // Knife switch dismiss (uses shared createKnifeSwitch utility)
  var switchWrap = document.createElement("div");
  switchWrap.className = "marquee-knife-wrap";

  var ks = createKnifeSwitch({
    size: "full",
    title: typeof t === "function" ? t("marquee.dismissTitle") : "Pull the switch to dismiss new books",
    onDismiss: function () { dismissMarquee(container); },
    delay: 400
  });

  switchWrap.appendChild(ks);
  container.appendChild(switchWrap);

  // Re-render on locale change so labels translate without page refresh
  if (!container._localeListener) {
    container._localeListener = function () {
      if (!container.classList.contains("hidden")) {
        buildMarquee(container, books);
      }
    };
    document.addEventListener("localeChanged", container._localeListener);
  }
}

/**
 * Dismiss the marquee and notify the server.
 * @param {HTMLElement} container - The marquee container element.
 */
function dismissMarquee(container) {
  container.classList.add("hidden");

  fetch("/api/user/new-books/dismiss", {
    method: "POST",
    credentials: "include",
  }).catch(function (err) {
    console.warn("Marquee: dismiss failed:", err.message);
  });
}
