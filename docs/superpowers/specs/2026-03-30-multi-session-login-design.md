# Multi-Session Login

**Date:** 2026-03-30
**Status:** Approved
**Branch:** v8-dev

## Problem

Logging in on a second device (or browser) kills the first session. The `Session.create_for_user()` method unconditionally deletes all existing sessions for the user before creating a new one. This is a friction point during cross-device/cross-browser testing and for users who access the library from multiple devices.

## Solution

Add an admin-controllable multi-session toggle: a global default plus a per-user override. When enabled for a user, new logins no longer invalidate existing sessions. Multiple sessions coexist independently.

### Non-Goals (Phase 1)

- **Device-scoped playback positions.** When two sessions stream the same book simultaneously, positions overwrite each other (last-write-wins). This is acceptable for the testing use case and rare in normal usage. Device-scoped positions (`user_id, audiobook_id, device_id`) can be added later if needed.
- **Device management UI.** No "view active sessions" or "revoke device" screen. Admins can already manage sessions via CLI (`audiobooks-auth sessions`). A UI can be added later.

## Design

### 1. Database Changes

#### `users` table — new column

```sql
ALTER TABLE users ADD COLUMN multi_session TEXT NOT NULL DEFAULT 'default';
```

Values:

- `'default'` — use global system setting
- `'yes'` — always allow multiple sessions
- `'no'` — always enforce single session

#### New `system_settings` table (auth DB)

```sql
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);
```

Seeded with: `('multi_session_default', 'false')`.

General-purpose key-value store for system-wide admin settings. Reusable for future global configuration.

#### `sessions` table — no changes

The table already supports multiple rows per `user_id` (no unique constraint on `user_id`). Single-session behavior is enforced by application logic, not schema.

#### Migration

Single migration step in `auth/database.py` `_run_migrations()`:

1. Add `multi_session` column to `users` (default `'default'`)
2. Create `system_settings` table if not exists
3. Seed `multi_session_default = 'false'` if not already present

### 2. Session Logic

#### Resolution function (single location)

```python
def _user_allows_multi_session(user) -> bool:
    if user.multi_session == 'yes':
        return True
    if user.multi_session == 'no':
        return False
    # 'default' — check system setting
    auth_db = get_auth_db()
    repo = SystemSettingsRepository(auth_db)
    return repo.get('multi_session_default') == 'true'
```

#### `Session.create_for_user()` change

New parameter: `allow_multi: bool = False`

```python
# Before (unconditional delete):
conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

# After (conditional):
if not allow_multi:
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
```

Default is `False` — preserves current behavior for any caller that doesn't pass the flag.

#### Call sites

Every place that calls `Session.create_for_user()` resolves the flag via `_user_allows_multi_session()`. There are 5 call sites in `auth.py`:

- `login()` — TOTP login (line ~902)
- `login_webauthn_complete()` — passkey login (line ~2479)
- `complete_magic_link()` — magic link login (line ~2968)
- `claim_credential_reset()` — credential reset flow (line ~1933)
- `claim_new_user_credentials()` — new user first login (line ~1979)

#### `SessionRepository.get_by_user_id()`

No changes needed. This method is used for admin session lookup, not auth validation. The auth middleware uses `get_by_token()`, which already works per-token regardless of how many sessions exist for the user.

### 3. API Endpoints

#### System settings (new)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/admin/settings` | GET | Admin | Get all system settings |
| `/api/admin/settings` | PATCH | Admin | Update one or more settings |

Request body for PATCH: `{"multi_session_default": "true"}`

#### User management (existing, extended)

The existing user update endpoint accepts `multi_session` as a settable field. No new endpoints needed — the `multi_session` column appears in user model responses automatically once added to the model.

#### Position and preference endpoints

No changes. Phase 1 is last-write-wins for concurrent sessions.

### 4. Back Office UI

Both controls live on the existing **Users tab** — no new pages or tabs.

#### Global toggle

Checkbox at the top of the Users tab: **"Allow multiple sessions by default"**. Reads from `GET /api/admin/settings`, writes via `PATCH /api/admin/settings`. Visible to admins only.

#### Per-user control

3-option selector in the user edit/detail view: **Default / Yes / No**. Displayed next to existing user fields (role, download permission). Saves via the existing user update endpoint.

### 5. Stale Session Cleanup

`auth_cleanup.py` already purges stale sessions (inactive > 30 minutes for non-persistent sessions). This continues to work unchanged. Multi-session users accumulate more session rows, but stale cleanup prunes them on the same schedule. No changes needed.

### 6. Backwards Compatibility

- **Upgrade:** All existing users get `multi_session = 'default'`, global default is `'false'`. Behavior after upgrade is identical to today. Multi-session only activates when an admin explicitly enables it.
- **Auth-disabled mode:** No impact. Sessions don't exist when `AUTH_ENABLED=false`.
- **Rollback:** If code is rolled back to a version without this feature, `create_for_user()` resumes its unconditional `DELETE`. The `multi_session` column is harmless dead data. The `system_settings` table is ignored.

## Files Changed

| File | Change |
|------|--------|
| `library/auth/models.py` | Add `multi_session` field to User, `SystemSettingsRepository` class, `allow_multi` param on `Session.create_for_user()` |
| `library/auth/database.py` | Migration: add column, create table, seed default |
| `library/auth/schema.sql` | Add `system_settings` table, add column to `users` |
| `library/backend/api_modular/auth.py` | `_user_allows_multi_session()` function, pass flag at all 5 `create_for_user()` call sites (TOTP, passkey, magic link, credential reset, new user claim), admin settings endpoints |
| `library/web-v2/utilities.html` | Global checkbox and per-user dropdown in Users tab |
| `library/web-v2/js/utilities.js` | Read/write system settings API, per-user multi_session field |

## Future Work (not in this spec)

- **Device-scoped positions:** `(user_id, audiobook_id, device_id)` key on `user_positions` with "resume from furthest?" prompt. Only needed if simultaneous same-book streaming becomes a real use case.
- **Active sessions UI:** "View/revoke active sessions" in user account or admin panel.
- **Session limit cap:** Instead of unlimited, cap at N concurrent sessions per user.
