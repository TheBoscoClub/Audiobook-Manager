# UX & Auth Improvements Design

**Date:** 2026-02-28
**Branch:** rnd/vox-grotto-rebrand

## Overview

Three related improvements to the user experience:

1. **Universal "Stay Logged In"** — All auth methods get a "Stay logged in" checkbox (default: checked)
2. **Clearer Nav Button Text** — "Sign In" → "Existing User Sign In", "Request Access" → "Request a User Account"
3. **Play Button Race Fix** — First click on Play sometimes silently fails due to iframe/parent initialization race

---

## 1. Universal "Stay Logged In" Checkbox

### Current State

| Auth Method | Has Checkbox | Default Behavior | User Control |
|---|---|---|---|
| TOTP | Yes | Session cookie (30 min timeout) | Checkbox (unchecked default) |
| Passkey/FIDO2 | No | Session cookie (30 min timeout) | None |
| Magic Link | No | Persistent (1-year cookie, 30-day timeout) | None (hardcoded) |

### Target State

| Auth Method | Has Checkbox | Default Behavior | User Control |
|---|---|---|---|
| TOTP | Yes | **Persistent** (checked default) | Checkbox |
| Passkey/FIDO2 | **Yes** | **Persistent** (checked default) | Checkbox |
| Magic Link | **Yes** | Persistent (checked default) | Checkbox |

### Changes Required

#### TOTP (1 line)
- `login.html`: Add `checked` attribute to the existing checkbox

#### Passkey/FIDO2 (3 files, ~12 lines)
- `login.html`: Show `rememberMeGroup` in passkey/FIDO2 branch; read checkbox value before WebAuthn call
- `webauthn.js`: Add `remember_me` parameter to `authenticate()` and `completeAuthentication()`; include in POST body to `/login/webauthn/complete`
- `auth.py` `/login/webauthn/complete`: Read `remember_me` from request body; pass to `Session.create_for_user()` and `set_session_cookie()`; default to `True` for backwards compat

#### Magic Link (4 files, ~12 lines)
- `login.html`: Show `rememberMeGroup` in magic link branch before calling `sendMagicLinkLogin()`; pass checkbox value
- `login.html` `sendMagicLinkLogin()`: Accept and include `remember_me` in POST body
- `auth.py` `/magic-link/login`: Read `remember_me`; append `&r=1` or `&r=0` to magic link URL in email
- `verify.html`: Extract `r` param from URL; include `remember_me` in verify POST body
- `auth.py` `/magic-link/verify`: Read `remember_me` from body (default `True`); replace hardcoded `True`

### Backwards Compatibility
- Old magic link emails (no `r` param) → defaults to `True` (no behavior change)
- Old WebAuthn JS (no `remember_me` in POST) → backend defaults to `True`

---

## 2. Clearer Nav Button Text

### Problem
Non-technical users confuse "Sign In" with "Request Access" — unclear which is for existing users.

### Changes (4 locations)

| File | Line | Current | New |
|---|---|---|---|
| `index.html` | 50 | `Sign In` | `Existing User Sign In` |
| `index.html` | 57 | `Request Access` | `Request a User Account` |
| `library.js` | 212 | `Sign In` | `Existing User Sign In` |
| `library.js` | 222 | `Request Access` | `Request a User Account` |

Button text only — no page titles, help text, or tutorial changes.

---

## 3. Play Button Race Condition Fix

### Root Cause

`shell.html` loads the iframe (`index.html`) in parallel with `shell.js` initialization. `shellPlayer` is created on the parent's `DOMContentLoaded` event (`shell.js:462-463`). If the iframe content loads from cache and the user clicks Play before the parent finishes:

```javascript
// library.js:2543
if (inIframe && window.parent.shellPlayer) {  // shellPlayer undefined → false
    window.parent.shellPlayer.playBook(book);
} else if (!inIframe) {                        // we ARE in iframe → false
    window.location.href = 'shell.html';
}
// → nothing happens, click silently dropped
```

### Fix

Replace silent failure with a short poll that waits for `shellPlayer` to initialize:

```javascript
function shellPlay(book, resume) {
    if (inIframe && window.parent.shellPlayer) {
        window.parent.shellPlayer.playBook(book);
    } else if (inIframe) {
        // Shell player not ready yet — wait and retry
        const poll = setInterval(() => {
            if (window.parent.shellPlayer) {
                clearInterval(poll);
                window.parent.shellPlayer.playBook(book);
            }
        }, 50);
        setTimeout(() => clearInterval(poll), 3000);
    } else {
        window.location.href = 'shell.html';
    }
}
```

Same pattern applied to `shellPause()` and any other `shellX()` helpers.

---

## Total Scope

~30 lines changed across 6 files. No new files, no schema changes, no new endpoints.

| File | Changes |
|---|---|
| `library/web-v2/login.html` | Show checkbox for all auth types, default checked, pass to magic link |
| `library/web-v2/js/webauthn.js` | Add `remember_me` param through auth flow |
| `library/web-v2/js/library.js` | Button text (2 lines), play race fix (~8 lines) |
| `library/web-v2/js/shell.js` | (no changes) |
| `library/web-v2/verify.html` | Extract `r` param, include in verify POST |
| `library/web-v2/index.html` | Button text (2 lines) |
| `library/backend/api_modular/auth.py` | Accept `remember_me` in webauthn complete + magic link endpoints |
