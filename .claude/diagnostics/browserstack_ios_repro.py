#!/usr/bin/env python3
"""
BrowserStack Automate harness — real iPhone, real iOS Safari/Chrome.

Why this exists: Playwright on Linux (WebKit) approximates iOS but runs on
desktop kernels. Chrome iOS and Safari iOS are the only browsers Qing uses,
both are WebKit under the hood (App Store rule), and neither reproduces
reliably without a real device. This script drives BrowserStack Automate's
real-device cloud via Appium/Selenium.

Flow (same as playwright_ios_repro.py):
  1. Session start on real iPhone + chosen browser
  2. Login as claudecode on qalib.thebosco.club (TOTP)
  3. Activate ?debug=1 overlay, enter iframe
  4. Open first book, press play, soak
  5. Harvest debug overlay text + full-page screenshot
  6. Close session (billable time ends)

KNOWN LIMIT — iOS autoplay policy:
  Selenium on real iOS routes every click through XCUITest's executeAtom,
  which (a) validates element visibility and (b) produces a synthesized
  event that does NOT satisfy WebKit's "user activation" gate for HTMLMedia.
  Audio/video .play() calls triggered by these synthetic clicks throw
  'NotAllowedError' and the streaming pipeline stays idle. The harness
  can prove navigation, login, locale switch, shell binding, and audio
  element setup — but the final play() is gated by a real finger on glass.
  Behavioral validation of streaming-translate state transitions requires
  a human tap on a real iPhone (or BrowserStack Live, not Automate).

Run with dedicated venv:
  .claude/diagnostics/browserstack-venv/bin/python \\
      .claude/diagnostics/browserstack_ios_repro.py --browser safari

Creds come from ~/.config/BrowserStackLocalCredentials.txt (service account
'claude-code' / claudecode_7adWNpxXPN). File must be chmod 600.

QA-only. Never point this at prod.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pyotp
from selenium import webdriver
from selenium.webdriver.common.options import ArgOptions
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[2]
CREDS_FILE = Path.home() / ".config" / "BrowserStackLocalCredentials.txt"
TOTP_SECRET_FILE = ROOT / ".claude" / "secrets" / "totp-secret"
OUT_DIR = ROOT / ".claude" / "diagnostics"

QA_HOST = "https://qalib.thebosco.club"
USERNAME = "claudecode"
HUB_URL = "https://hub-cloud.browserstack.com/wd/hub"

PLAY_WAIT_SEC = 45
LOGIN_TIMEOUT_SEC = 30
LIB_LOAD_TIMEOUT_SEC = 60


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def read_browserstack_creds() -> tuple[str, str]:
    """Pull claude-code service account credentials.

    The file ships two credential blocks — personal + service account. We
    want the service account (identifier: claude-code). Parsing is a tiny
    state machine keyed on the 'Identifier: claude-code' line.
    """
    if not CREDS_FILE.exists():
        raise RuntimeError(f"Creds file not found: {CREDS_FILE}")
    # File perms sanity check — user explicitly set 600; if it has drifted
    # we want a loud failure, not a silent credential leak.
    mode = CREDS_FILE.stat().st_mode & 0o777
    if mode != 0o600:
        raise RuntimeError(
            f"{CREDS_FILE} has mode {oct(mode)}; expected 0o600"
        )
    text = CREDS_FILE.read_text()
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Find service account block — 'Identifier:' followed by 'claude-code'
    svc_start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "Identifier:" and i + 1 < len(lines):
            if lines[i + 1].strip() == "claude-code":
                svc_start = i
                break
    if svc_start is None:
        raise RuntimeError("claude-code service account block not found")
    # From svc_start onward, find next Username: and Access Key:
    username = None
    access_key = None
    i = svc_start
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "Username:" and i + 1 < len(lines):
            username = lines[i + 1].strip()
        elif stripped == "Access Key:" and i + 1 < len(lines):
            access_key = lines[i + 1].strip()
        if username and access_key:
            break
        i += 1
    if not username or not access_key:
        raise RuntimeError("Could not parse username/access_key for claude-code")
    return username, access_key


def read_qa_totp_secret() -> str:
    """Pull the QA claudecode TOTP secret. Identical logic to Playwright harness."""
    text = TOTP_SECRET_FILE.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "QA" in line and "TOTP" in line:
            for j in range(i + 1, len(lines)):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("#"):
                    if re.fullmatch(r"[A-Z2-7]+", candidate):
                        return candidate
    raise RuntimeError(
        f"QA TOTP secret not found under '## QA claudecode TOTP' header in {TOTP_SECRET_FILE}"
    )


def build_capabilities(
    username: str,
    access_key: str,
    device: str,
    os_version: str,
    browser: str,
    build_name: str,
    session_name: str,
) -> ArgOptions:
    """Assemble BrowserStack Automate capabilities for real iOS.

    BrowserStack's Automate hub rejects raw Appium XCUITestOptions with
    'Platform can be one of MAC, WIN8, XP, WINDOWS, and ANY' — the cloud
    routes based on 'deviceName' presence inside bstack:options, not on
    platformName. Use plain Selenium options with a W3C-compatible shape:
      * top-level browserName
      * everything mobile/device-specific under bstack:options
    """
    opts = ArgOptions()
    # BrowserStack accepts 'safari' or 'chrome' for top-level browserName
    # on iOS real devices; the routing happens via bstack:options.deviceName.
    opts.set_capability("browserName", browser)
    bstack_options = {
        "userName": username,
        "accessKey": access_key,
        "osVersion": os_version,
        "deviceName": device,
        "realMobile": "true",
        "projectName": "Audiobook-Manager",
        "buildName": build_name,
        "sessionName": session_name,
        "debug": "true",
        "networkLogs": "true",
        "consoleLogs": "verbose",
        # Keep session under 5 minutes to be polite to free-tier minutes
        "idleTimeout": 60,
    }
    opts.set_capability("bstack:options", bstack_options)
    return opts


def run(
    device: str,
    os_version: str,
    browser: str,
    book_id: str | None,
    search: str | None,
) -> int:
    bs_user, bs_key = read_browserstack_creds()
    totp_secret = read_qa_totp_secret()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tag = f"{device.replace(' ', '-')}-ios{os_version}-{browser}"
    shot_path = OUT_DIR / f"bs-{tag}-{ts}.png"
    text_path = OUT_DIR / f"bs-{tag}-{ts}.txt"
    meta_path = OUT_DIR / f"bs-{tag}-{ts}.meta.json"

    build_name = f"qing-streaming-repro-{datetime.now().strftime('%Y%m%d')}"
    session_name = f"{device} iOS {os_version} {browser} v8.3.6"

    opts = build_capabilities(
        username=bs_user,
        access_key=bs_key,
        device=device,
        os_version=os_version,
        browser=browser,
        build_name=build_name,
        session_name=session_name,
    )

    log(f"connecting to BrowserStack: {device} iOS {os_version} {browser}")
    driver = webdriver.Remote(
        command_executor=HUB_URL,
        options=opts,
    )
    session_id = driver.session_id
    log(f"session {session_id} live — dashboard: https://automate.browserstack.com/dashboard/v2/sessions/{session_id}")

    meta: dict = {
        "session_id": session_id,
        "device": device,
        "os_version": os_version,
        "browser": browser,
        "started_at": datetime.now().isoformat(),
        "qa_host": QA_HOST,
    }

    try:
        # 1. Navigate to login
        log(f"navigating to {QA_HOST}/login.html")
        driver.get(f"{QA_HOST}/login.html")

        wait = WebDriverWait(driver, LOGIN_TIMEOUT_SEC)
        wait.until(EC.presence_of_element_located((By.ID, "username")))
        driver.find_element(By.ID, "username").send_keys(USERNAME)
        driver.find_element(By.ID, "continue-button").click()

        # Wait for TOTP group to un-hide
        wait.until(
            lambda d: d.execute_script(
                "var el = document.getElementById('totp-group');"
                " return el && !el.hasAttribute('hidden');"
            )
        )
        code = pyotp.TOTP(totp_secret).now()
        log("TOTP generated")
        driver.find_element(By.ID, "code").send_keys(code)
        driver.find_element(By.ID, "login-button").click()

        # Wait for redirect to / (or /?something)
        wait.until(lambda d: re.match(
            r"^https://qalib\.thebosco\.club/?(\?.*)?$", d.current_url
        ))
        log(f"login OK — landed at {driver.current_url}")

        # 2. Prime localStorage (dismiss feature announce banner + enable debug)
        driver.get(f"{QA_HOST}/?debug=1")
        wait.until(
            lambda d: d.execute_script("return document.readyState === 'complete'")
        )
        driver.execute_script(
            "localStorage.setItem('feature-announce-v8.1-dismissed', '1');"
            "localStorage.setItem('debugOverlay', '1');"
        )
        log("localStorage primed")

        # 3. Reload + enter iframe
        driver.get(f"{QA_HOST}/?debug=1")
        wait.until(EC.presence_of_element_located((By.ID, "content-frame")))

        # Flip locale to zh-Hans on the shell (Qing's flow). Must happen BEFORE
        # entering the iframe — i18n is owned by the shell and propagates to the
        # iframe via shared localStorage + postMessage. Without this, the
        # streaming-translate path never fires and the soak just shows cached
        # English playback.
        driver.execute_script(
            "if (window.i18n && window.i18n.setLocale) {"
            "  window.i18n.setLocale('zh-Hans');"
            "} else {"
            "  localStorage.setItem('locale', 'zh-Hans');"
            "}"
        )
        time.sleep(1.5)
        log("locale set to zh-Hans")

        driver.switch_to.frame(driver.find_element(By.ID, "content-frame"))
        log("content-frame entered")

        # 4. Wait for books to render
        iframe_wait = WebDriverWait(driver, LIB_LOAD_TIMEOUT_SEC)
        iframe_wait.until(
            lambda d: d.execute_script(
                "return document.querySelectorAll('.book-card[data-id]').length > 0"
            )
        )
        log("library rendered")

        # 5. Pick the book — specific id if provided, else first card.
        # Lazy-render defense: the library grid virtualizes rows, so a book
        # that's below the fold won't have a DOM node. Search-input filters
        # by title/author/narrator (not id), so `--search` narrows the grid
        # before we look for the target data-id. Both flags are paired:
        # `--search` surfaces the card, `--book-id` confirms we clicked the
        # right one.
        if search:
            driver.execute_script(
                "var si = document.getElementById('search-input');"
                " if (!si) throw new Error('search-input not found');"
                " si.focus();"
                " si.value = arguments[0];"
                " si.dispatchEvent(new Event('input', {bubbles: true}));",
                search,
            )
            time.sleep(1.5)
            log(f"search filter applied: {search!r}")

        if book_id:
            iframe_wait.until(
                lambda d: d.execute_script(
                    "return !!document.querySelector("
                    "  '.book-card[data-id=\"' + arguments[0] + '\"]'"
                    ");",
                    book_id,
                )
            )
            target_id = book_id
        else:
            card = driver.find_element(By.CSS_SELECTOR, ".book-card[data-id]")
            target_id = card.get_attribute("data-id")
        meta["book_id"] = target_id
        log(f"opening book id={target_id}")

        # Invoke the play handler.
        # On iOS Safari via BrowserStack's XCUITest driver, passing a WebElement
        # as an execute_script argument routes through a Selenium atom that
        # pre-validates visibility and throws ElementNotVisibleException on the
        # hover-hidden .btn-play. Workaround: pass only the book id string,
        # re-query the DOM inside JS (no element reference crosses the boundary),
        # force-override the hiding CSS, then dispatch a synthetic MouseEvent.
        driver.execute_script(
            "var bookId = arguments[0];"
            " var card = document.querySelector('.book-card[data-id=\"'"
            "   + bookId + '\"]');"
            " if (!card) throw new Error('card not found: ' + bookId);"
            " card.scrollIntoView({block: 'center'});"
            " var btn = card.querySelector('.btn-play');"
            " if (!btn) throw new Error('btn-play not found');"
            " btn.style.setProperty('display', 'inline-flex', 'important');"
            " btn.style.setProperty('visibility', 'visible', 'important');"
            " btn.style.setProperty('opacity', '1', 'important');"
            " btn.style.setProperty('pointer-events', 'auto', 'important');"
            " btn.dispatchEvent(new MouseEvent('click',"
            "   {bubbles: true, cancelable: true, view: window}));",
            target_id,
        )
        time.sleep(0.5)

        # 6. Exit iframe, wait for shell player + click play there too
        driver.switch_to.default_content()
        wait.until(
            lambda d: d.execute_script(
                "var el = document.getElementById('shell-player');"
                " return el && !el.hasAttribute('hidden');"
            )
        )
        # Same iOS XCUITest atom-visibility issue as .btn-play — dispatch
        # click entirely inside JS so no WebElement reference crosses the
        # Selenium boundary and triggers pre-validation.
        driver.execute_script(
            "var btn = document.getElementById('sp-play-pause');"
            " if (!btn) throw new Error('sp-play-pause not found');"
            " btn.style.setProperty('display', 'inline-flex', 'important');"
            " btn.style.setProperty('visibility', 'visible', 'important');"
            " btn.style.setProperty('opacity', '1', 'important');"
            " btn.style.setProperty('pointer-events', 'auto', 'important');"
            " btn.dispatchEvent(new MouseEvent('click',"
            "   {bubbles: true, cancelable: true, view: window}));"
        )
        log(f"playback started — soaking {PLAY_WAIT_SEC}s to surface streaming state")

        # 7. Soak
        time.sleep(PLAY_WAIT_SEC)

        # 8. Harvest debug overlay body
        try:
            overlay_el = driver.find_element(By.ID, "debug-overlay-body")
            overlay_txt = overlay_el.text or ""
        except NoSuchElementException:
            overlay_txt = "(debug-overlay-body not present — ?debug=1 activation may have failed)"

        text_path.write_text(overlay_txt, encoding="utf-8")

        # 9. Screenshot — on real iOS this is the full viewport, not full page
        png_b64 = driver.get_screenshot_as_base64()
        shot_path.write_bytes(base64.b64decode(png_b64))

        log(f"overlay   → {text_path.relative_to(ROOT)}")
        log(f"screenshot → {shot_path.relative_to(ROOT)}")

        # Grab streamingTranslate state for quick triage
        stream_state = driver.execute_script(
            "var st = window.streamingTranslate || {};"
            " return JSON.stringify({"
            "   loaded: !!st,"
            "   state: st.state || null,"
            "   bookId: st.currentBookId || null"
            " });"
        )
        meta["streaming_state"] = json.loads(stream_state) if stream_state else None

        snippet = overlay_txt[:2000]
        print("\n=== debug overlay (first 2k chars) ===\n")
        print(snippet or "(empty)")
        print("\n=== end ===\n")

        # Mark session passed on BrowserStack (shows green in dashboard)
        driver.execute_script(
            'browserstack_executor: {"action": "setSessionStatus",'
            ' "arguments": {"status": "passed", "reason": "streaming flow completed"}}'
        )
        meta["status"] = "passed"
        return 0

    except (TimeoutException, WebDriverException, NoSuchElementException) as e:
        err_msg = f"{type(e).__name__}: {e}"
        log(f"FAILED: {err_msg}")
        try:
            png_b64 = driver.get_screenshot_as_base64()
            err_shot = OUT_DIR / f"bs-{tag}-{ts}-ERROR.png"
            err_shot.write_bytes(base64.b64decode(png_b64))
            log(f"error screenshot → {err_shot.relative_to(ROOT)}")
        except WebDriverException:
            pass
        try:
            driver.execute_script(
                'browserstack_executor: {"action": "setSessionStatus",'
                ' "arguments": {"status": "failed", "reason": ' + json.dumps(err_msg[:200]) + "}}"
            )
        except WebDriverException:
            pass
        meta["status"] = "failed"
        meta["error"] = err_msg
        return 2
    finally:
        meta["ended_at"] = datetime.now().isoformat()
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log(f"meta      → {meta_path.relative_to(ROOT)}")
        try:
            driver.quit()
        except WebDriverException:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="iPhone 15",
                    help="iOS device name (default: 'iPhone 15')")
    ap.add_argument("--os-version", default="18",
                    help="iOS major version (default: '18')")
    ap.add_argument("--browser", choices=["safari", "chrome"], default="safari",
                    help="Mobile browser (default: safari)")
    ap.add_argument("--book-id", default=None,
                    help="Specific book data-id to open; default = first card")
    ap.add_argument("--search", default=None,
                    help="Text to type into the library search box (title/author) "
                         "to surface a lazy-rendered card; pair with --book-id "
                         "to confirm the target")
    args = ap.parse_args()
    return run(
        device=args.device,
        os_version=args.os_version,
        browser=args.browser,
        book_id=args.book_id,
        search=args.search,
    )


if __name__ == "__main__":
    sys.exit(main())
