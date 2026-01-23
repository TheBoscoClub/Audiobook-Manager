# Security Branch Integration Plan

## Branch: `rd/secure-remote-access`

This document tracks the requirements and gaps for integrating the security/auth branch into main.

---

## Current State

**Branch commits**: 18 commits implementing:
- Phase 0: Caddy-based dev infrastructure
- Phase 1-2: User auth with backup code recovery
- Phase 3: Per-user playback position tracking, conditional auth decorators
- Phase 4: Auth UI with mobile-responsive design, magic link recovery
- Phase 5: Notifications and contact system
- Phase 6: Hardening, security audit, WebAuthn/Passkey support

**Version on branch**: 4.0.3
**Main branch version**: 4.1.2

---

## Identified Gaps

### 1. Missing: First-User Bootstrap / Default Admin

**Problem**: No mechanism to create the initial admin user.

**Current state**:
- CLI tool `audiobook-user add --admin` exists
- Web registration creates "pending" users awaiting approval
- No admin exists to approve anyone

**Required**:
- [ ] Option A: First registered user automatically becomes admin
- [ ] Option B: CLI-only admin creation (current, but needs documentation)
- [ ] Option C: Environment variable to set initial admin username

**Recommendation**: Option A with safeguard - first user becomes admin ONLY if no users exist.

---

### 2. Missing: Admin Approval UI/API

**Problem**: Admins cannot see or approve pending registration requests.

**Current state**:
- `pending_registrations` table stores requests
- No endpoint to list pending registrations
- No endpoint to approve/deny requests
- No admin UI for user management

**Required**:
- [ ] `GET /api/admin/pending` - List pending registrations
- [ ] `POST /api/admin/approve/<id>` - Approve registration
- [ ] `POST /api/admin/deny/<id>` - Deny registration
- [ ] `GET /api/admin/users` - List all users
- [ ] `POST /api/admin/users/<id>/toggle-admin` - Promote/demote admin
- [ ] Admin UI page for user management

---

### 3. Missing: CLI Approve Command

**Problem**: No CLI command to approve pending users.

**Required**:
```bash
audiobook-user approve <username>    # Approve pending registration
audiobook-user deny <username>       # Deny and delete pending registration
audiobook-user pending               # List pending registrations
```

---

### 4. Missing: audiobooks User/Group Creation in Install

**Problem**: Install scripts assume `audiobooks` user/group exists but don't create it.

**Current state**:
- Scripts reference `SERVICE_USER="audiobooks"` and `SERVICE_GROUP="audiobooks"`
- No `useradd`/`groupadd` commands in any install script
- User must manually create before installation

**Required**:
Add to `install.sh` (system installation mode):
```bash
# Create audiobooks group if not exists
if ! getent group audiobooks >/dev/null; then
    sudo groupadd --system audiobooks
fi

# Create audiobooks user if not exists
if ! getent passwd audiobooks >/dev/null; then
    sudo useradd --system --gid audiobooks --shell /usr/sbin/nologin \
        --home-dir /var/lib/audiobooks --comment "Audiobook Library Service" audiobooks
fi

# Add installer to audiobooks group
sudo usermod -aG audiobooks "$USER"
echo "NOTE: Log out and back in for group membership to take effect"
```

**Important**:
- Application users (web auth) are stored in SQLite only - NO Linux accounts
- Only the service account `audiobooks` and installer need Linux access

---

### 5. API Port Configuration Mismatch

**Problem**: Web UI expects API at same origin (`/api`), but dev config uses different ports.

**Current state**:
- Dev config: API on port 6001, Web on port 9090
- Production: API on port 5001, Web on port 8443
- Web JS uses `API_BASE = '/api'` (relative)

**Required**:
- [ ] Document that proxy_server.py must be used (handles `/api/*` proxying)
- [ ] Or: Add CORS support and configurable API_BASE for development

---

## Integration Checklist

### Pre-merge Requirements

- [ ] Implement first-user-is-admin bootstrap
- [ ] Add admin approval endpoints
- [ ] Add admin UI for user management
- [ ] Add CLI `approve`/`deny`/`pending` commands
- [ ] Add audiobooks user/group creation to install.sh
- [ ] Test fresh installation flow end-to-end
- [ ] Update README with auth documentation
- [ ] Update CHANGELOG

### Testing Requirements

- [ ] Fresh install on clean VM creates audiobooks user/group
- [ ] First user registration succeeds and becomes admin
- [ ] Admin can approve subsequent user registrations
- [ ] WebAuthn/Passkey flow works
- [ ] TOTP flow works
- [ ] Magic link recovery works
- [ ] Backup code recovery works
- [ ] Session management works
- [ ] Per-user playback positions work

---

## Architecture Notes

### User Types

| Type | Storage | Linux Account | Purpose |
|------|---------|---------------|---------|
| `audiobooks` (service) | `/etc/passwd` | Yes (nologin shell) | Runs systemd services |
| Installer user | `/etc/passwd` | Yes | Must be in audiobooks group |
| App users | SQLite `auth.db` | **No** | Web authentication only |

### Security Model

- App users have NO shell access (SQLite-only)
- Service account has nologin shell
- Auth database encrypted with SQLCipher (AES-256)
- Sessions stored as hashed tokens
- Passwords never stored (TOTP/Passkey only)

---

## Files Changed on Branch

Key files to review during merge:
- `library/auth/` - New auth module
- `library/backend/api_modular/auth.py` - Auth API endpoints
- `library/web-v2/login.html`, `register.html`, etc. - Auth UI
- `library/web-v2/js/auth.js` - Auth frontend logic
- `library/backend/auth-dev.db` - Dev database (don't merge)

---

*Last updated: 2026-01-23*
