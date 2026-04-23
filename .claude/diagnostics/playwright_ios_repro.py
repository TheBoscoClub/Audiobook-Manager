#!/usr/bin/env python3
"""
Playwright WebKit harness — reproduce the reported Chrome-iOS streaming bug on CachyOS.

Chrome iOS is Safari under the hood (App Store rule forces all iOS browsers to
WebKit). WebKit on Linux is the closest local approximation we have to iOS
browsers without a Mac.

Flow:
  1. Launch WebKit with iPhone 14 device emulation
  2. Log in as `claudecode` on the QA host from $AUDIOBOOKS_QA_HOST
     (TOTP from .claude/secrets/totp-secret)
  3. Navigate to /?debug=1 — the debug overlay activates
  4. Open first book, switch locale to zh-Hans, press play
  5. Wait through buffering window, capture screenshot + overlay text
  6. Dump artifacts to .claude/diagnostics/repro-<ts>.{png,txt}

Also supports --browser firefox and --browser chromium for cross-browser sanity.

QA-only. Never point this at prod.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pyotp
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Arch/CachyOS ships `flite` without the six voice libs WebKit links against
# (libflite_cmu_us_awb.so.1 etc.). /usr/local/lib contains compatibility
# symlinks pointing to libflite_cmu_us_slt.so.1 — but ldconfig registers
# them under the *target's* SONAME, not the symlink filename. Solution:
# prepend /usr/local/lib to LD_LIBRARY_PATH so the dynamic loader resolves
# by filename instead of SONAME.
_existing_ld = os.environ.get("LD_LIBRARY_PATH", "")
os.environ["LD_LIBRARY_PATH"] = (
    "/usr/local/lib" + (":" + _existing_ld if _existing_ld else "")
)

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / ".claude" / "secrets" / "totp-secret"
OUT_DIR = ROOT / ".claude" / "diagnostics"
QA_HOST = os.environ.get("AUDIOBOOKS_QA_HOST", "https://qa.example.com")
USERNAME = "claudecode"
# Buffer window long enough to see phase transitions (buffering -> streaming/error)
PLAY_WAIT_SEC = 45


def read_qa_totp_secret() -> str:
    """Pull the QA claudecode TOTP secret from the secrets file.

    Format: multi-line file with `## QA claudecode TOTP` header followed by
    a base32 secret on its own line. Other environments (prod/dev) have
    their own headers and secrets.
    """
    text = SECRETS.read_text()
    # Grab the line following the QA header, skipping blank lines
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "QA" in line and "TOTP" in line:
            for j in range(i + 1, len(lines)):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("#"):
                    # base32 secrets are [A-Z2-7]+
                    if re.fullmatch(r"[A-Z2-7]+", candidate):
                        return candidate
    raise RuntimeError(
        f"QA TOTP secret not found under '## QA claudecode TOTP' header in {SECRETS}"
    )


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run(browser_name: str, headless: bool) -> int:
    secret = read_qa_totp_secret()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot_path = OUT_DIR / f"repro-{browser_name}-{ts}.png"
    text_path = OUT_DIR / f"repro-{browser_name}-{ts}.txt"
    console_path = OUT_DIR / f"repro-{browser_name}-{ts}.console.log"
    console_lines: list[str] = []

    with sync_playwright() as p:
        if browser_name == "webkit":
            browser_type = p.webkit
            device_ctx = p.devices["iPhone 14"]
        elif browser_name == "firefox":
            browser_type = p.firefox
            device_ctx = {"viewport": {"width": 390, "height": 844}}
        elif browser_name == "chromium":
            browser_type = p.chromium
            device_ctx = p.devices["iPhone 14"]
        else:
            raise ValueError(f"unknown browser: {browser_name}")

        log(f"launching {browser_name} (headless={headless})")
        browser = browser_type.launch(headless=headless)
        context = browser.new_context(
            **device_ctx,
            ignore_https_errors=True,
            locale="zh-Hans-CN",
        )
        page = context.new_page()

        def on_console(msg):
            line = f"[{msg.type}] {msg.text}"
            console_lines.append(line)

        def on_pageerror(err):
            console_lines.append(f"[pageerror] {err}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

        try:
            # 1. Login
            log(f"navigating to {QA_HOST}/login.html")
            page.goto(f"{QA_HOST}/login.html", wait_until="domcontentloaded")
            page.fill("#username", USERNAME)
            page.click("#continue-button")
            # Wait for TOTP field to unhide
            page.wait_for_selector("#totp-group:not([hidden])", timeout=15_000)
            code = pyotp.TOTP(secret).now()
            log("TOTP generated (rotates every 30s)")
            page.fill("#code", code)
            page.click("#login-button")

            # Successful login redirects to /
            page.wait_for_url(re.compile(rf"{re.escape(QA_HOST)}/?(\?.*)?$"),
                              timeout=20_000)
            log("login OK")

            # 2. Pre-seed localStorage on the QA origin so the first-visit
            # feature-announce banner doesn't obscure the library. The field user saw it
            # once and dismissed it; fresh Playwright contexts hit it every run.
            page.goto(f"{QA_HOST}/?debug=1", wait_until="domcontentloaded")
            page.evaluate(
                "localStorage.setItem('feature-announce-v8.1-dismissed', '1');"
                "localStorage.setItem('debugOverlay', '1');"
            )
            log("localStorage primed (feature-announce dismissed, debug on)")

            # Reload so the banner module sees the dismiss flag at startup.
            # shell.html is an outer chrome; the library lives inside
            # <iframe id="content-frame" name="content-frame" src="index.html">
            # Same origin so localStorage is shared, but DOM is NOT — all
            # .book-card queries must target the iframe's Frame object.
            page.goto(f"{QA_HOST}/?debug=1", wait_until="domcontentloaded",
                      timeout=45_000)
            page.wait_for_selector("iframe#content-frame", timeout=15_000)
            # Prefer name-based lookup; fall back to element_handle for frames
            # whose name attribute hasn't been wired yet.
            content_frame = page.frame(name="content-frame")
            if content_frame is None:
                iframe_el = page.query_selector("iframe#content-frame")
                content_frame = iframe_el.content_frame() if iframe_el else None
            if content_frame is None:
                raise RuntimeError("content-frame iframe not reachable")
            log("content-frame acquired")

            try:
                content_frame.wait_for_function(
                    "document.querySelectorAll('.book-card[data-id]').length > 0",
                    timeout=60_000,
                )
            except PWTimeout:
                # Dump what IS in the iframe DOM to diagnose
                sample = content_frame.evaluate(
                    "() => {"
                    "  const grid = document.getElementById('books-grid');"
                    "  const libSection = document.getElementById('library-section');"
                    "  const lib = window.library;"
                    "  return {"
                    "    bookCardTotal: document.querySelectorAll('.book-card').length,"
                    "    dataIdTotal: document.querySelectorAll('[data-id]').length,"
                    "    booksGridExists: !!grid,"
                    "    booksGridChildren: grid ? grid.childElementCount : null,"
                    "    booksGridInnerLen: grid ? grid.innerHTML.length : null,"
                    "    librarySectionVisible: libSection ? libSection.offsetParent !== null : null,"
                    "    windowLibraryType: typeof lib,"
                    "    libraryBooksCount: lib && lib.books ? lib.books.length : null,"
                    "    libraryCurrentView: lib ? lib.currentView : null,"
                    "    libraryCurrentGrouping: lib ? lib.currentGrouping : null,"
                    "    libraryIsLoading: lib ? lib.isLoading : null,"
                    "    documentReadyState: document.readyState,"
                    "    bodyTextSnippet: document.body.innerText.slice(0, 500),"
                    "    urlNow: location.href,"
                    "  };"
                    "}"
                )
                diag = OUT_DIR / f"repro-{browser_name}-{ts}-dom-diag.json"
                import json
                diag.write_text(json.dumps(sample, indent=2))
                log(f"DOM diag → {diag.relative_to(ROOT)}")
                raise
            log("library rendered (cards attached in iframe)")

            # 3. Flip locale via i18n API on the SHELL (main page) — the shell
            # owns the locale switcher and propagates to the iframe via
            # postMessage / shared localStorage.
            page.evaluate("window.i18n && window.i18n.setLocale('zh-Hans')")
            time.sleep(1.5)
            log("locale set to zh-Hans")

            # 4. Open first book — card lives in the iframe. The card itself
            # opens a details view; the play button inside it calls
            # shellPlay(...) which escapes to the parent shell and is what
            # surfaces #shell-player on the main page. Click the button.
            first_card = content_frame.locator(".book-card[data-id]").first
            first_card.scroll_into_view_if_needed(timeout=5_000)
            book_id = first_card.get_attribute("data-id")
            play_btn_card = first_card.locator(".btn-play")
            play_btn_card.wait_for(state="visible", timeout=10_000)
            log(f"opening book id={book_id} (clicking .btn-play in iframe)")
            play_btn_card.click()

            # 5. Wait for shell-player to surface + press play. shellPlay
            # auto-starts playback, but the shell exposes #sp-play-pause so we
            # also exercise the user-facing button path explicitly.
            page.wait_for_selector("#shell-player:not([hidden])", timeout=30_000)
            play_btn = page.locator("#sp-play-pause")
            play_btn.wait_for(state="visible", timeout=10_000)
            log("clicking play")
            play_btn.click()

            # 6. Soak time — this is where the field-user flow died at ~5min
            log(f"soaking for {PLAY_WAIT_SEC}s to surface streaming state")
            time.sleep(PLAY_WAIT_SEC)

            # 7. Harvest overlay body + screenshot
            overlay_txt = ""
            try:
                overlay_txt = page.locator("#debug-overlay-body").text_content(
                    timeout=5_000
                ) or ""
            except PWTimeout:
                overlay_txt = "(debug overlay element not found — ?debug=1 may have failed to activate)"

            text_path.write_text(overlay_txt, encoding="utf-8")
            page.screenshot(path=str(shot_path), full_page=True)
            console_path.write_text("\n".join(console_lines), encoding="utf-8")

            log(f"screenshot → {shot_path.relative_to(ROOT)}")
            log(f"overlay   → {text_path.relative_to(ROOT)}")
            log(f"console   → {console_path.relative_to(ROOT)} ({len(console_lines)} lines)")

            # Quick self-assessment
            snippet = overlay_txt[:2000]
            print("\n=== debug overlay (first 2k chars) ===\n")
            print(snippet or "(empty)")
            print("\n=== end ===\n")
            return 0

        except Exception as e:
            err_shot = OUT_DIR / f"repro-{browser_name}-{ts}-ERROR.png"
            try:
                page.screenshot(path=str(err_shot), full_page=True)
                log(f"error screenshot → {err_shot.relative_to(ROOT)}")
            except Exception:
                pass
            console_path.write_text("\n".join(console_lines), encoding="utf-8")
            log(f"FAILED: {type(e).__name__}: {e}")
            return 2
        finally:
            context.close()
            browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--browser",
        choices=["webkit", "firefox", "chromium"],
        default="webkit",
        help="engine to drive (default: webkit — closest to iOS Safari/Chrome)",
    )
    ap.add_argument(
        "--headed",
        action="store_true",
        help="show the browser window (default headless)",
    )
    args = ap.parse_args()
    return run(args.browser, headless=not args.headed)


if __name__ == "__main__":
    sys.exit(main())
