/**
 * Feature Announcement Banner — bilingual, one-time dismissible.
 *
 * Shows a prominent Art Deco banner announcing major new features.
 * Dismissed state persists in localStorage so each user sees it once.
 * Uses safe DOM construction (createElement + textContent only).
 */
(function () {
  "use strict";

  var DISMISS_KEY = "feature-announce-v8.1-dismissed";
  var CONTAINER_ID = "feature-announce";

  // Already dismissed?
  if (localStorage.getItem(DISMISS_KEY)) return;

  function build() {
    var target = document.getElementById(CONTAINER_ID);
    if (!target) return;

    var banner = document.createElement("div");
    banner.className = "feature-announce";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-label", "Feature announcement");

    // Dismiss button
    var dismiss = document.createElement("button");
    dismiss.className = "announce-dismiss";
    dismiss.textContent = "\u00D7";
    dismiss.title = "Dismiss";
    dismiss.addEventListener("click", function () {
      localStorage.setItem(DISMISS_KEY, "1");
      banner.style.animation = "none";
      banner.style.transition = "opacity 0.3s, transform 0.3s";
      banner.style.opacity = "0";
      banner.style.transform = "translateY(-8px)";
      setTimeout(function () { banner.remove(); }, 350);
    });
    banner.appendChild(dismiss);

    // English headline
    var headEn = document.createElement("div");
    headEn.className = "announce-headline";
    headEn.textContent = "Now Available in Chinese";
    banner.appendChild(headEn);

    // Chinese headline
    var headZh = document.createElement("div");
    headZh.className = "announce-headline-zh";
    headZh.textContent = "\u73B0\u5DF2\u652F\u6301\u4E2D\u6587";
    banner.appendChild(headZh);

    // Divider
    var div1 = document.createElement("span");
    div1.className = "announce-divider";
    banner.appendChild(div1);

    // English body
    var bodyEn = document.createElement("div");
    bodyEn.className = "announce-body";
    bodyEn.textContent =
      "The Library now speaks Simplified Chinese. " +
      "Switch languages with the globe icon in the header. " +
      "Bilingual subtitles and a full transcript panel are generated " +
      "automatically when you play any audiobook.";
    banner.appendChild(bodyEn);

    // Chinese body
    var bodyZh = document.createElement("div");
    bodyZh.className = "announce-body-zh";
    bodyZh.textContent =
      "\u56FE\u4E66\u9986\u73B0\u5DF2\u652F\u6301\u7B80\u4F53\u4E2D\u6587\u3002" +
      "\u70B9\u51FB\u9876\u90E8\u6807\u9898\u680F\u7684\u5730\u7403\u56FE\u6807\u5207\u6362\u8BED\u8A00\u3002" +
      "\u64AD\u653E\u4EFB\u4F55\u6709\u58F0\u4E66\u65F6\uFF0C" +
      "\u53CC\u8BED\u5B57\u5E55\u548C\u5B8C\u6574\u7684\u8BD1\u6587\u9762\u677F\u5C06\u81EA\u52A8\u751F\u6210\u3002";
    banner.appendChild(bodyZh);

    // Divider
    var div2 = document.createElement("span");
    div2.className = "announce-divider";
    banner.appendChild(div2);

    // Feature highlights row
    var features = document.createElement("div");
    features.className = "announce-features";

    var items = [
      { icon: "\uD83C\uDF10", en: "Multi-Language", zh: "\u591A\u8BED\u8A00" },
      { icon: "\uD83D\uDCDC", en: "Subtitles",     zh: "\u5B57\u5E55" },
      { icon: "\uD83D\uDCD6", en: "Transcript",     zh: "\u8BD1\u6587\u9762\u677F" },
      { icon: "\uD83D\uDD0D", en: "CJK Search",     zh: "\u4E2D\u6587\u641C\u7D22" }
    ];

    items.forEach(function (item) {
      var card = document.createElement("div");
      card.className = "announce-feature";

      var icon = document.createElement("span");
      icon.className = "announce-feature-icon";
      icon.textContent = item.icon;
      card.appendChild(icon);

      var labelEn = document.createElement("span");
      labelEn.className = "announce-feature-label";
      labelEn.textContent = item.en;
      card.appendChild(labelEn);

      var labelZh = document.createElement("span");
      labelZh.className = "announce-feature-label-zh";
      labelZh.textContent = item.zh;
      card.appendChild(labelZh);

      features.appendChild(card);
    });

    banner.appendChild(features);
    target.appendChild(banner);
  }

  // Build when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
