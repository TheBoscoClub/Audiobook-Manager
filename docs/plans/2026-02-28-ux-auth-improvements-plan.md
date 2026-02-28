# UX & Auth Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add "Stay logged in" checkbox to all auth methods, clarify nav button text, and fix play button race condition.

**Architecture:** Frontend-only changes for button text and play fix. Frontend+backend changes for remember-me — the checkbox state flows from login.html through each auth method's chain to `Session.create_for_user()` and `set_session_cookie()`.

**Tech Stack:** Vanilla JS (frontend), Python/Flask (backend)

---

### Task 1: TOTP — Default checkbox to checked

**Files:**
- Modify: `library/web-v2/login.html:107`

**Step 1: Change checkbox default**

In `login.html:107`, add the `checked` attribute:

```html
<!-- OLD -->
<input type="checkbox" id="remember-me" name="remember_me">

<!-- NEW -->
<input type="checkbox" id="remember-me" name="remember_me" checked>
```

**Step 2: Commit**

```bash
git add library/web-v2/login.html
git commit -m "feat(auth): default 'Stay logged in' to checked for TOTP"
```

---

### Task 2: Passkey/FIDO2 — Show checkbox and pipe remember_me through

**Files:**
- Modify: `library/web-v2/login.html:449-464` (show checkbox, read value)
- Modify: `library/web-v2/login.html:516-524` (pass to WebAuthn.authenticate)
- Modify: `library/web-v2/js/webauthn.js:296-351` (accept and forward remember_me)
- Modify: `library/backend/api_modular/auth.py:2218-2242` (accept and use remember_me)

**Step 1: Show checkbox in passkey/FIDO2 branch**

In `login.html`, after `changeUsernameLink.hidden = false;` (line 464), add:

```javascript
                    changeUsernameLink.hidden = false;
                    rememberMeGroup.hidden = false;  // ADD THIS LINE
```

**Step 2: Read checkbox before WebAuthn call**

In `login.html:516-518`, change:

```javascript
// OLD
if (currentAuthType === 'passkey' || currentAuthType === 'fido2') {
    // WebAuthn authentication flow
    const result = await WebAuthn.authenticate(username);

// NEW
if (currentAuthType === 'passkey' || currentAuthType === 'fido2') {
    // WebAuthn authentication flow
    const remember_me = rememberMeCheckbox.checked;
    const result = await WebAuthn.authenticate(username, remember_me);
```

**Step 3: Add remember_me to webauthn.js authenticate()**

In `webauthn.js:345-351`, change:

```javascript
// OLD
    async authenticate(username) {
        const beginResult = await this.startAuthentication(username);
        return await this.completeAuthentication(beginResult, username);
    },

// NEW
    async authenticate(username, remember_me = true) {
        const beginResult = await this.startAuthentication(username);
        return await this.completeAuthentication(beginResult, username, remember_me);
    },
```

**Step 4: Add remember_me to webauthn.js completeAuthentication()**

In `webauthn.js:296`, change the signature:

```javascript
// OLD
    async completeAuthentication(beginResult, username) {

// NEW
    async completeAuthentication(beginResult, username, remember_me = true) {
```

In `webauthn.js:325-329`, add `remember_me` to the POST body:

```javascript
// OLD
            body: JSON.stringify({
                username: username,
                credential: encodedCredential,
                challenge: beginResult.challenge
            })

// NEW
            body: JSON.stringify({
                username: username,
                credential: encodedCredential,
                challenge: beginResult.challenge,
                remember_me: remember_me
            })
```

**Step 5: Accept remember_me in backend `/login/webauthn/complete`**

In `auth.py:2218-2242`, change:

```python
# OLD (lines 2218-2224)
    # Create session
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
    )

# NEW
    # Create session
    remember_me = data.get("remember_me", True)
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
    )
```

And line 2242:

```python
# OLD
    return set_session_cookie(response, token)

# NEW
    return set_session_cookie(response, token, remember_me=remember_me)
```

**Step 6: Commit**

```bash
git add library/web-v2/login.html library/web-v2/js/webauthn.js library/backend/api_modular/auth.py
git commit -m "feat(auth): add 'Stay logged in' checkbox for Passkey/FIDO2"
```

---

### Task 3: Magic Link — Show checkbox and pipe remember_me through email URL

**Files:**
- Modify: `library/web-v2/login.html:445-448` (show checkbox, pass to sendMagicLinkLogin)
- Modify: `library/web-v2/login.html:698-721` (accept remember_me, include in POST)
- Modify: `library/backend/api_modular/auth.py:2498-2537` (read remember_me, encode in URL)
- Modify: `library/web-v2/verify.html:172-221` (extract r param, include in verify POST)
- Modify: `library/backend/api_modular/auth.py:2641-2701` (read remember_me, replace hardcoded True)

**Step 1: Show checkbox and pass to sendMagicLinkLogin**

In `login.html:445-448`, change:

```javascript
// OLD
                if (currentAuthType === 'magic_link') {
                    // Magic link flow — auto-submit to send login link
                    changeUsernameLink.hidden = false;
                    await sendMagicLinkLogin(username);

// NEW
                if (currentAuthType === 'magic_link') {
                    // Magic link flow — show remember-me, then auto-submit
                    changeUsernameLink.hidden = false;
                    rememberMeGroup.hidden = false;
                    await sendMagicLinkLogin(username, rememberMeCheckbox.checked);
```

**Step 2: Accept remember_me in sendMagicLinkLogin and include in POST**

In `login.html:698-706`, change:

```javascript
// OLD
        async function sendMagicLinkLogin(username) {
            const magicLinkSentGroup = document.getElementById('magic-link-sent-group');
            clearError(errorMessage);

            try {
                const response = await fetch(`${API_BASE}/auth/magic-link/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ identifier: username })
                });

// NEW
        async function sendMagicLinkLogin(username, remember_me = true) {
            const magicLinkSentGroup = document.getElementById('magic-link-sent-group');
            clearError(errorMessage);

            try {
                const response = await fetch(`${API_BASE}/auth/magic-link/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ identifier: username, remember_me })
                });
```

**Step 3: Backend — read remember_me and encode in magic link URL**

In `auth.py:2535-2537`, change:

```python
# OLD
    recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=15)

    magic_link_url = f"/verify.html?token={raw_token}"

# NEW
    remember_me = data.get("remember_me", True)

    recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=15)

    r_flag = "1" if remember_me else "0"
    magic_link_url = f"/verify.html?token={raw_token}&r={r_flag}"
```

**Step 4: verify.html — extract r param and include in verify POST**

In `verify.html:214-221`, change:

```javascript
// OLD
        const params = new URLSearchParams(window.location.search);
        const token = params.get('token');
        const activate = params.get('activate') === '1';

        if (token) {
            // Auto-verify token from URL
            verifyToken(token, activate);

// NEW
        const params = new URLSearchParams(window.location.search);
        const token = params.get('token');
        const activate = params.get('activate') === '1';
        const rememberMe = params.get('r') !== '0';  // default true for old links

        if (token) {
            // Auto-verify token from URL
            verifyToken(token, activate, rememberMe);
```

In `verify.html:172-175`, change the function signature and body construction:

```javascript
// OLD
        async function verifyToken(token, activate) {
            try {
                const body = { token };
                if (activate) body.activate = true;

// NEW
        async function verifyToken(token, activate, rememberMe = true) {
            try {
                const body = { token, remember_me: rememberMe };
                if (activate) body.activate = true;
```

**Step 5: Backend — read remember_me in /magic-link/verify, replace hardcoded True**

In `auth.py:2681-2682`, change:

```python
# OLD
    session, raw_token = Session.create_for_user(
        db, user.id, user_agent, ip_address, remember_me=True
    )

# NEW
    remember_me = data.get("remember_me", True)
    session, raw_token = Session.create_for_user(
        db, user.id, user_agent, ip_address, remember_me=remember_me
    )
```

In `auth.py:2701`, change:

```python
# OLD
    return set_session_cookie(response, raw_token, remember_me=True)

# NEW
    return set_session_cookie(response, raw_token, remember_me=remember_me)
```

**Step 6: Commit**

```bash
git add library/web-v2/login.html library/web-v2/verify.html library/backend/api_modular/auth.py
git commit -m "feat(auth): add 'Stay logged in' checkbox for magic link"
```

---

### Task 4: Nav button text — Clarify for non-technical users

**Files:**
- Modify: `library/web-v2/index.html:50,57`
- Modify: `library/web-v2/js/library.js:212,222`

**Step 1: Update index.html**

Line 50:
```html
<!-- OLD -->
<span class="utilities-text">Sign In</span>

<!-- NEW -->
<span class="utilities-text">Existing User Sign In</span>
```

Line 57:
```html
<!-- OLD -->
<span class="utilities-text">Request Access</span>

<!-- NEW -->
<span class="utilities-text">Request a User Account</span>
```

**Step 2: Update library.js**

Line 212:
```javascript
// OLD
signInLink.textContent = 'Sign In';

// NEW
signInLink.textContent = 'Existing User Sign In';
```

Line 222:
```javascript
// OLD
requestLink.textContent = 'Request Access';

// NEW
requestLink.textContent = 'Request a User Account';
```

**Step 3: Commit**

```bash
git add library/web-v2/index.html library/web-v2/js/library.js
git commit -m "feat(ui): clarify nav button text for non-technical users"
```

---

### Task 5: Play button race fix — Wait for shellPlayer initialization

**Files:**
- Modify: `library/web-v2/js/library.js:2542-2562`

**Step 1: Replace shellPlay with retry-aware version**

Replace `library.js:2542-2562`:

```javascript
// OLD
function shellPlay(book, resume) {
    if (inIframe && window.parent.shellPlayer) {
        // Same-origin: call shell player directly (no postMessage needed)
        window.parent.shellPlayer.playBook(book);
    } else if (!inIframe) {
        // Direct index.html access — redirect to shell
        window.location.href = 'shell.html';
    }
}

function shellPause() {
    if (inIframe && window.parent.shellPlayer) {
        window.parent.shellPlayer.audio.pause();
    }
}

function shellResume() {
    if (inIframe && window.parent.shellPlayer) {
        window.parent.shellPlayer.audio.play();
    }
}

// NEW

/**
 * Wait for shellPlayer to initialize, then call the action.
 * Handles the race where iframe loads before parent's DOMContentLoaded.
 */
function whenShellReady(action) {
    if (window.parent.shellPlayer) {
        action(window.parent.shellPlayer);
        return;
    }
    const poll = setInterval(() => {
        if (window.parent.shellPlayer) {
            clearInterval(poll);
            action(window.parent.shellPlayer);
        }
    }, 50);
    setTimeout(() => clearInterval(poll), 3000);
}

function shellPlay(book, resume) {
    if (inIframe) {
        whenShellReady(sp => sp.playBook(book));
    } else {
        window.location.href = 'shell.html';
    }
}

function shellPause() {
    if (inIframe) {
        whenShellReady(sp => sp.audio.pause());
    }
}

function shellResume() {
    if (inIframe) {
        whenShellReady(sp => sp.audio.play());
    }
}
```

**Step 2: Check if shellSeek also needs the same treatment**

Read `library.js` from line 2564 onward and apply `whenShellReady` to any other `shellX()` helpers that check `window.parent.shellPlayer`.

**Step 3: Commit**

```bash
git add library/web-v2/js/library.js
git commit -m "fix: resolve play button race condition with iframe initialization"
```

---

### Task 6: Verify all changes work together

**Step 1: Check Python syntax**

```bash
python -c "import py_compile; py_compile.compile('library/backend/api_modular/auth.py', doraise=True)"
```

**Step 2: Run existing unit tests**

```bash
cd library && python -m pytest tests/ -x -q 2>&1 | tail -20
```

**Step 3: Manual smoke test checklist (on test VM)**

- [ ] TOTP login: checkbox shown, **checked by default**, unchecking gives session cookie
- [ ] Passkey login: checkbox now shown, checked by default, works both ways
- [ ] Magic link login: checkbox shown before email sent, preference carried through to session
- [ ] Old magic link email (no `r` param): still creates persistent session (backwards compat)
- [ ] Nav buttons: "Existing User Sign In" and "Request a User Account" visible
- [ ] Play button: single click launches player (test with cache primed)
- [ ] Play button: works normally when shell is already initialized

**Step 4: Final commit (if any fixes needed)**

```bash
git add -u
git commit -m "fix: address issues found during verification"
```
