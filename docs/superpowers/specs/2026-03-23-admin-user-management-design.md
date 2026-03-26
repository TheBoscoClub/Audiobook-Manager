# Admin User Management — Design Spec

**Date**: 2026-03-23
**Status**: Approved
**Branch**: `user-management` (to be created)
**Target version**: TBD (next minor release)

## Problem

Administering user accounts currently requires direct access to the encrypted SQLCipher auth database via CLI or a SQL editor. This creates real risk of corruption from typos, terminal crashes, or editor bugs. It also blocks account creation when email delivery fails (e.g., mac.com blocking thebosco.club), since the existing invite flow depends on email.

## Goals

1. Admins can create users and set up auth methods entirely from the web UI
2. Users can manage their own non-critical account settings (username, email, auth method, credentials) via self-service
3. All account changes are audit-logged with admin notifications for critical changes
4. Eliminate any routine reason to open the auth database directly

## Architecture

### Approach: Extend Back Office + Shell Self-Service

- **Back Office — new USERS tab**: Full admin user lifecycle management. The existing user management section moves OUT of the overcrowded System tab into its own dedicated tab.
- **Shell — My Account modal**: Lightweight self-service overlay accessible from the shell header. No page navigation; player keeps playing underneath.
- **API layer**: New endpoints under `/auth/admin/` (admin) and `/auth/account/` (self-service), sharing backend logic with authorization as the differentiator.
- **Audit log**: New `audit_log` table in the auth database, surfaced in the existing Activity tab.

## Permissions Matrix

| Action | User (self) | Admin (any user) | Audit logged | Admin notified |
|--------|:-----------:|:----------------:|:------------:|:--------------:|
| Create user + auth method | — | Yes | Yes | — |
| Change username | Yes | Yes | Yes | Yes |
| Change email | Yes | Yes | Yes | No |
| Switch auth method | Yes | Yes | Yes | Yes |
| Reset credentials | Yes | Yes | Yes | Yes |
| Toggle admin/download | — | Yes | Yes | — |
| Delete user | Yes (own) | Yes | Yes | Yes |

"Admin notified" = in-app toast/badge + email to all admins with an email on file.

## Component Design

### 1. Admin "Create User" Flow (Back Office USERS Tab)

**Form fields:**

| Field | Required | Notes |
|-------|----------|-------|
| Username | Yes | 3-24 chars, alphanumeric + hyphens (matches existing DB constraint) |
| Email | No | Required only if auth method is Magic Link |
| Auth Method | Yes | Radio: TOTP (default), Magic Link, Passkey |
| Roles | Yes | Checkboxes: Admin, Download (both off by default) |

**Per auth method:**

- **TOTP**: Server generates secret. Admin sees QR code + manual entry key. "Download QR" button saves `username_MMDD-HMS.png`. Setup info remains viewable on the USERS tab until the user's first successful login, then redacted.

- **Magic Link**: Server creates user with email. No admin-side secret needed. User enters email at login to receive magic link.

- **Passkey**: Server creates user in "pending passkey" state with a one-time claim token. Admin sees/copies the claim URL (also downloadable). Reuses existing `claim.html` flow since passkeys can only be registered on the user's own device.

**API:**

```text
POST /auth/admin/users/create
Body: { username, email?, auth_method, is_admin, can_download }
Response: { user_id, setup_data }
  - TOTP: setup_data = { secret, qr_uri, manual_key }
  - Magic Link: setup_data = {}
  - Passkey: setup_data = { claim_token, claim_url, expires_at }
```

**Setup info retrieval** (for admins returning to the USERS tab later):

```text
GET /auth/admin/users/<id>/setup-info
Returns: same setup_data structure
404 after first successful login (secret/claim redacted)
```

### 2. User Self-Service "My Account" (Shell Modal)

**Access point:** User icon or username text in shell header bar. Click opens modal overlay.

**Modal sections:**

| Section | Contents |
|---------|----------|
| Profile | Username (inline-editable), Email (inline-editable), Member since |
| Authentication | Current method badge, "Switch method" button |
| Credentials | "Reset credentials" button |
| Danger Zone | "Delete my account" button (red, separated) |

**Interaction patterns:**

- **Edit username/email**: Click field → type → save. Triggers audit log + admin notification (username only).
- **Switch auth method**: Sub-panel with three options. New method must be fully configured before old method deactivates — no lockout window.
- **Reset credentials**: Confirmation → regenerate. TOTP shows new QR. Passkey triggers re-registration. Magic Link confirms email.
- **Delete account**: One button → one confirm dialog → immediate deletion → session ends → redirect to login.

**Deletion confirmation text:**
> "This will permanently delete your account and all of your listening history. You will likely experience intermittent swattings and harassment. Can't be helped — this is normal and should be expected, because you already knew who was behind this bullshit webapp when you signed up in the first place."

**Self-service API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth/account` | Get own profile |
| `PUT` | `/auth/account/username` | Change own username |
| `PUT` | `/auth/account/email` | Change own email |
| `PUT` | `/auth/account/auth-method` | Switch own auth method |
| `POST` | `/auth/account/reset-credentials` | Reset own credentials |
| `DELETE` | `/auth/account` | Delete own account |

**Documentation updates required:** README.md, AUTH_RUNBOOK.md, ARCHITECTURE.md, any user-facing tutorial/onboarding content.

### 3. Audit Logging

**New table in auth database:**

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    target_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    details TEXT
);
```

`actor_id` and `target_id` are nullable with `ON DELETE SET NULL` so audit records survive user deletion. The `details` JSON always includes the username as a denormalized backup for deleted-user lookups.

- `actor_id == target_id` → self-service action
- `actor_id != target_id` → admin action

**Surfaces in:** The new USERS tab in Back Office (consolidates all user-related features in one place). Table with timestamp, actor, target, action (human-readable), details (expandable old → new). Filterable by action type and user. Most recent first.

**Logged actions:**

| Action | Details |
|--------|---------|
| `create_user` | auth method, roles assigned |
| `change_username` | old → new |
| `change_email` | old → new |
| `switch_auth_method` | old → new method |
| `reset_credentials` | method reset |
| `toggle_roles` | old → new state |
| `delete_account` | username at deletion time |

**Retention:** No auto-purge. Small text rows, tiny user count.

### 4. Admin Notifications

Three-tier notification stack for critical changes (username, auth method, credentials, self-deletion):

1. **In-app toast + badge**: Always, for all admins. Toast on next load or WebSocket push if connected. Badge count on USERS tab.

2. **Email alert**: For all admins with an email on their account. Sent via existing Resend SMTP path (`library@thebosco.club`). Short, factual:
   > Subject: [Audiobook Library] Account change: bosco changed username
   > Body: User "bosco" changed their username to "bosco2" at 2026-03-23 18:15 UTC. Review in Back Office → Users → Audit Log.

3. **Audit log highlight**: Critical actions get visual indicator (amber left border) persisting until admin has viewed them.

### 5. Admin API Endpoints (Complete)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/auth/admin/users` | List all users (existing, serves USERS tab) |
| `POST` | `/auth/admin/users/create` | Create user + auth method |
| `PUT` | `/auth/admin/users/<id>/roles` | Toggle admin/download |
| `PUT` | `/auth/admin/users/<id>/username` | Change username |
| `PUT` | `/auth/admin/users/<id>/email` | Change email |
| `PUT` | `/auth/admin/users/<id>/auth-method` | Switch auth method |
| `POST` | `/auth/admin/users/<id>/reset-credentials` | Reset credentials |
| `DELETE` | `/auth/admin/users/<id>` | Delete user |
| `GET` | `/auth/admin/audit-log` | Paginated audit log |
| `GET` | `/auth/admin/users/<id>/setup-info` | Onboarding QR/claim URL |

All mutating endpoints return the updated user object + audit log entry ID.

## Files Affected

| File | Changes |
|------|---------|
| `library/auth/schema.sql` | Add `audit_log` table |
| `library/auth/models.py` | Add AuditLog model, audit helper functions |
| `library/backend/api_modular/auth.py` | New admin + self-service endpoints |
| `library/web-v2/utilities.html` | New USERS tab markup, audit log in Activity tab |
| `library/web-v2/js/utilities.js` | USERS tab logic, audit log rendering, notification handling |
| `library/web-v2/shell.html` | My Account trigger in header |
| `library/web-v2/shell.css` | My Account modal styles |
| `library/web-v2/js/shell.js` (or new `account.js`) | My Account modal logic |
| `library/web-v2/css/utilities.css` | USERS tab styles, notification badges |
| `docs/ARCHITECTURE.md` | Component updates |
| `docs/AUTH_RUNBOOK.md` | Self-service flows, admin creation flow |
| `README.md` | Feature list update |

## Branch Strategy

Feature branch `user-management` off `main`. Full build and test on `test-audiobook-cachyos` before merge. Auth system is security-critical — no partial merges.

## Security Considerations

- All admin endpoints require `@admin_required` decorator (existing)
- All self-service endpoints require `@login_required` (existing)
- TOTP secrets shown only to admin at creation and until first login — then permanently redacted
- Claim tokens expire (existing behavior)
- Audit log is append-only from application perspective
- Self-deletion requires active session (can't be triggered anonymously)
- Auth method switch requires successful setup of new method before old deactivates — this is a two-step flow (initiate + confirm) for TOTP and Passkey; single-step for Magic Link
- **Last-admin guard**: Cannot delete the last admin account (self-service or admin endpoint). Prevents unmanageable system state.
- "Email" in the UI and API maps to the existing `recovery_email` column — no new column needed
- TOTP setup-info retrieval uses the existing `auth_credential` column; redaction check is `last_login IS NULL`
- Notification viewed-state tracked via `last_audit_seen_id` column on users table (admin only) — entries with `id > last_audit_seen_id` show the amber highlight
