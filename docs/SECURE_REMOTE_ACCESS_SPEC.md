# Secure Remote Access Design Specification

**Version:** 1.1.0 (Updated for v6.0.0 dual-mode security)
**Branch:** Merged to `main`
**Last Updated:** 2026-02-18

> **Related Documentation:**
>
> - [README — Authentication Section](../README.md#authentication-v50) — User-facing setup guide
> - [Architecture — Auth Module](ARCHITECTURE.md#authentication-module-architecture) — System design and database schema
> - [Auth Runbook](AUTH_RUNBOOK.md) — Operational procedures
> - [Auth Failure Modes](AUTH_FAILURE_MODES.md) — Troubleshooting guide
**Status:** Implementation Complete (Phase 6 Complete) - Ready for Go Live

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [System Architecture](#3-system-architecture)
4. [Security Model](#4-security-model)
5. [User Model](#5-user-model)
6. [Authentication](#6-authentication)
7. [Session Management](#7-session-management)
8. [Authorization](#8-authorization)
9. [Data Model](#9-data-model)
10. [API Design](#10-api-design)
11. [User Interface](#11-user-interface)
12. [Notifications and Contact](#12-notifications-and-contact)
13. [Backup and Recovery](#13-backup-and-recovery)
14. [Operational Considerations](#14-operational-considerations)
15. [Implementation Phases](#15-implementation-phases)
16. [Open Questions](#16-open-questions)
17. [Appendices](#17-appendices)

---

## 1. Executive Summary

### 1.1 Purpose

Enable secure remote access to the Vox Grotto library for a small group of trusted users (friends and family) while maintaining strict security, privacy, and isolation guarantees.

### 1.2 Key Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Zero PII Storage** | Email/phone used ephemerally for verification, never persisted |
| **Passwordless Auth** | Passkeys, FIDO2, and TOTP only - no passwords to leak |
| **Defense in Depth** | Multiple security layers, each independent |
| **Least Privilege** | Users access only what they need, nothing more |
| **Complete Isolation** | Users cannot access OS, other users' data, or admin functions |
| **Fail Secure** | On error, deny access rather than grant it |

### 1.3 User Base

- **Scale:** ~12-16 users maximum
- **Trust Level:** Personally known, but not trusted to avoid mistakes
- **Access Pattern:** Remote, from anywhere on the internet
- **Admin:** Single administrator (local access only)

---

## 2. Goals and Non-Goals

### 2.1 Goals

1. **Secure Authentication**
   - Passwordless authentication (Passkey, FIDO2, TOTP)
   - Self-service registration with ephemeral verification
   - Single active session per user

2. **Library Access**
   - Browse audiobook catalog
   - Stream audio files
   - Track per-user listening positions
   - Download files (with explicit permission)

3. **Privacy Protection**
   - No personally identifiable information stored
   - Per-user data isolation
   - Encrypted credential storage

4. **Administrative Control**
   - User management (create, modify, delete)
   - Permission control (download access)
   - Notification system for announcements
   - Contact/inbox for user communication

5. **Operational Security**
   - TLS encryption for all traffic
   - Rate limiting and abuse prevention
   - Comprehensive logging (without PII)
   - Backup and recovery procedures

### 2.2 Non-Goals

1. **Multi-tenancy** - This is not a SaaS product; single admin, shared library
2. **Public Registration** - Users must complete verification; no open signup
3. **Social Features** - No user-to-user interaction, reviews, or sharing
4. **Offline Access** - Streaming only; no offline sync (except explicit downloads)
5. **Mobile Apps** - Web-only; no native iOS/Android apps
6. **Audible Sync for Users** - Position sync with Audible is admin-only

---

## 3. System Architecture

### 3.1 High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    INTERNET                                              │
└─────────────────────────────────────────────────────────────────────────────────────────┘
         │
         │ library.thebosco.club (DNS via Cloudflare)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              ROUTER (Port 443 forwarded)                                 │
└─────────────────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SERVER                                                │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │                              CADDY (Port 443)                                      │ │
│  │  • TLS termination (Let's Encrypt auto-renewal)                                   │ │
│  │  • Reverse proxy                                                                  │ │
│  │  • Rate limiting                                                                  │ │
│  │  • Security headers                                                               │ │
│  │  • Optional additional access control                                            │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
│              │                              │                              │            │
│              ▼                              ▼                              ▼            │
│  ┌─────────────────────────────────────────────────┐      ┌─────────────────────┐    │
│  │          FLASK API (Port 5001)                   │      │   STATIC ASSETS     │    │
│  │                                                  │      │                     │    │
│  │   ┌──────────────┐    ┌──────────────────────┐  │      │   • Web UI          │    │
│  │   │  Auth BP      │    │  Library BP           │  │      │   • CSS/JS          │    │
│  │   │  /auth/*      │    │  /api/*               │  │      │   • Cover images    │    │
│  │   │               │    │                       │  │      │                     │    │
│  │   │ • Login/out   │    │ • Browse catalog      │  │      │                     │    │
│  │   │ • Register    │    │ • Stream audio        │  │      │                     │    │
│  │   │ • Session     │    │ • Admin (guarded by   │  │      │                     │    │
│  │   │ • TOTP/Passkey│    │   admin_or_localhost) │  │      │                     │    │
│  │   └──────────────┘    └──────────────────────┘  │      │                     │    │
│  └─────────────────────────────────────────────────┘      └─────────────────────┘    │
│              │                              │                                          │
│              ▼                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│  │                              DATABASES                                           │  │
│  │                                                                                  │  │
│  │   ┌─────────────────────────┐      ┌─────────────────────────────────────┐      │  │
│  │   │  auth.db (SQLCipher)    │      │  audiobooks.db (SQLite)             │      │  │
│  │   │  ENCRYPTED AT REST      │      │  Standard (content not sensitive)   │      │  │
│  │   │                         │      │                                     │      │  │
│  │   │  • users                │      │  • audiobooks                       │      │  │
│  │   │  • sessions             │      │  • genres                           │      │  │
│  │   │  • user_positions       │      │  • supplements                      │      │  │
│  │   │  • notifications        │      │                                     │      │  │
│  │   │  • inbox                │      │                                     │      │  │
│  │   │  • pending_registrations│      │                                     │      │  │
│  │   └─────────────────────────┘      └─────────────────────────────────────┘      │  │
│  │                                                                                  │  │
│  └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│  │                         EXISTING SERVICES (Unchanged)                            │  │
│  │                         Admin-only, localhost access                             │  │
│  │                                                                                  │  │
│  │   • grotto-converter.service                                                  │  │
│  │   • grotto-mover.service                                                      │  │
│  │   • grotto-downloader.timer                                                   │  │
│  │   • Back Office (utilities.html)                                                 │  │
│  └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐  │
│  │                              FILE STORAGE                                        │  │
│  │                                                                                  │  │
│  │   /srv/audiobooks/Library/    → Opus files (streamable to auth'd users)         │  │
│  │   /srv/audiobooks/Sources/    → AAXC files (NEVER exposed)                      │  │
│  │   /srv/audiobooks/.covers/    → Cover art (served to auth'd users)              │  │
│  └─────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Network Architecture

| Component | Port | Exposure | Protocol |
|-----------|------|----------|----------|
| Caddy/Reverse Proxy | 443 | Internet | HTTPS (TLS 1.2+) |
| Flask API (auth + library) | 5001 | localhost only | HTTP |
| HTTPS Proxy | 8443 | localhost/LAN | HTTPS (self-signed) |
| Admin endpoints | 5001 | `admin_or_localhost` guarded | HTTP |

> **Note (v6.0+):** Auth is integrated as a Flask Blueprint (`auth_bp`) within the main API on port 5001, not a separate service. Admin endpoints use the `admin_or_localhost` decorator — in remote mode (`AUTH_ENABLED=true`) they require authenticated admin; in standalone mode they restrict to localhost.

### 3.3 DNS Configuration

> **Note:** The examples below use `library.thebosco.club` as a concrete deployment example. Replace with your own domain.

| Record | Type | Value | Purpose |
|--------|------|-------|---------|
| `thebosco.club` | A | Squarespace IP | Main website (unchanged) |
| `library.thebosco.club` | A | Home server IP | Audiobook library |
| MX records | MX | Proton | Email routing (unchanged) |

### 3.4 Dynamic DNS

- **Provider:** Cloudflare (free tier)
- **Update Method:** Cron job every 5 minutes
- **Fallback:** Manual update after extended outage

---

## 4. Security Model

### 4.1 Threat Model

#### 4.1.1 Threat Actors

| Actor | Capability | Motivation |
|-------|------------|------------|
| Automated scanners | Port scanning, CVE probing | Opportunistic exploitation |
| Credential stuffing bots | Large-scale login attempts | Account takeover |
| Opportunistic hackers | Known vulnerability exploitation | Data theft, system access |
| Curious users | Authorized access, boundary testing | Accidental exposure |

#### 4.1.2 Assets to Protect

| Asset | Sensitivity | Protection |
|-------|-------------|------------|
| Auth credentials | Critical | Encrypted at rest (SQLCipher) |
| User sessions | High | Secure tokens, single-session |
| Listening positions | Medium | Per-user isolation |
| Audio files | Low | Authentication required |
| Source files (AAXC) | High | Never exposed |
| Admin functions | Critical | Localhost only |

### 4.2 Defense Layers

```text
LAYER 1: NETWORK
├── Caddy as sole entry point
├── TLS 1.2+ only
├── Only port 443 exposed
├── Rate limiting
└── Optional: Cloudflare proxy

LAYER 2: AUTHENTICATION
├── No passwords
├── Passkey/FIDO2/TOTP only
├── Magic links expire in 15 minutes
├── Single session per user
└── Cryptographically random tokens

LAYER 3: AUTHORIZATION
├── Remote users: library functions only
├── Back Office: admin_or_localhost (admin auth or localhost)
├── Downloads: explicit permission required
├── Positions: own data only
└── Admin: admin_or_localhost (admin auth or localhost)

LAYER 4: DATA PROTECTION
├── Auth database encrypted (SQLCipher)
├── No PII stored
├── Pseudonymous usernames
├── No passwords to leak
└── Per-user data isolation

LAYER 5: OPERATIONAL
├── Minimal attack surface
├── Sanitized logs
├── Suspicious activity alerts
├── BTRFS snapshots
└── Least privilege
```

### 4.3 Security Headers

```yaml
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(), camera=()
```

### 4.4 Rate Limiting

| Endpoint | Limit | Window | Action on Exceed |
|----------|-------|--------|------------------|
| `/auth/login` | 5 attempts | 15 minutes | Block IP temporarily |
| `/auth/register` | 3 attempts | 1 hour | Block IP temporarily |
| `/auth/magic-link` | 3 requests | 15 minutes | Block IP temporarily |
| `/api/*` (authenticated) | 100 requests | 1 minute | 429 response |
| `/api/stream/*` | 10 concurrent | Per user | Queue additional |

---

## 5. User Model

### 5.1 User Types

| Type | Capabilities | Access Method |
|------|--------------|---------------|
| **Admin** | Full system access, user management, Back Office | Authenticated admin (remote) or localhost (standalone) |
| **Library User** | Browse, stream, positions, contact admin | Remote (authenticated) |
| **Library User + Download** | Above + download opus files | Remote (authenticated) |

### 5.2 User Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | INTEGER | Auto | Primary key |
| `username` | TEXT | Yes | 5-16 printable characters, unique |
| `auth_type` | TEXT | Yes | 'passkey', 'fido2', or 'totp' |
| `auth_credential` | BLOB | Yes | Encrypted credential data |
| `can_download` | BOOLEAN | No | Download permission (default: false) |
| `is_admin` | BOOLEAN | No | Admin flag (default: false) |
| `created_at` | TIMESTAMP | Auto | Account creation time |
| `last_login` | TIMESTAMP | No | Last successful login |
| `recovery_email` | TEXT | No | Optional recovery email (user's choice to store) |
| `recovery_phone` | TEXT | No | Optional recovery phone (user's choice to store) |
| `recovery_enabled` | BOOLEAN | No | Whether contact-based recovery is enabled |

### 5.3 Recovery Model

Users choose at registration whether to store contact information for recovery:

| Recovery Option | Contact Stored | Recovery Methods |
|-----------------|----------------|------------------|
| **No Contact Info** | No | Backup codes only (8 single-use codes) |
| **Email Stored** | Yes (encrypted) | Backup codes + Magic link to email |
| **Phone Stored** | Yes (encrypted) | Backup codes + Magic link via SMS |
| **Both Stored** | Yes (encrypted) | Backup codes + Magic link (user's choice) |

**Important Security Note:**

- Backup codes are ALWAYS generated regardless of recovery setting
- Users who choose not to store contact info are explicitly warned that losing both their authenticator AND all backup codes means the account is unrecoverable
- Admin can delete the account and user can re-register, but listening positions are lost

### 5.4 Username Requirements

- **Length:** 5-16 characters
- **Characters:** Any printable ASCII (0x20-0x7E)
- **Uniqueness:** Case-sensitive (`Bob` ≠ `bob`)
- **Validation:** Checked at registration, enforced at database level

---

## 6. Authentication

### 6.1 Authentication Methods

| Method | Description | Use Case |
|--------|-------------|----------|
| **Passkey** | WebAuthn/FIDO2 platform authenticator | Preferred, most secure |
| **FIDO2 Key** | Hardware security key (YubiKey, Titan) | High security |
| **TOTP** | Time-based one-time password (authenticator app) | Universal fallback |
| **Magic Link** | One-time URL via email/SMS | When primary unavailable |

### 6.2 Registration Flow

```text
User visits /register
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: Choose Username                                         │
│                                                                  │
│  Username: [_______________] (5-16 characters)                  │
│                                                                  │
│  Verification method:                                            │
│    ○ Email: [_______________]                                   │
│    ○ SMS:   [_______________]                                   │
│                                                                  │
│  [ Continue ]                                                    │
└─────────────────────────────────────────────────────────────────┘
         │
         │  Server generates verification token
         │  Sends link via email/SMS
         │  Email/phone held in memory ONLY (never persisted)
         │  Token stored as hash in pending_registrations
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: Click Verification Link (within 15 minutes)            │
│                                                                  │
│  Link format: /auth/verify?token=<256-bit-random>               │
└─────────────────────────────────────────────────────────────────┘
         │
         │  Token validated and consumed (single-use)
         │  Email/phone discarded from memory
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: Choose Authentication Method                           │
│                                                                  │
│  How would you like to log in?                                  │
│                                                                  │
│    ○ Passkey (recommended)                                      │
│      Use Face ID, fingerprint, or device PIN                    │
│                                                                  │
│    ○ Security Key                                               │
│      Use a YubiKey or similar FIDO2 device                      │
│                                                                  │
│    ○ Authenticator App                                          │
│      Use Google Authenticator, Authy, etc.                      │
│                                                                  │
│  [ Set Up ]                                                      │
└─────────────────────────────────────────────────────────────────┘
         │
         │  Passkey: WebAuthn registration ceremony
         │  FIDO2: WebAuthn registration ceremony
         │  TOTP: Generate secret, display QR code, verify code
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Registration Complete                                          │
│                                                                  │
│  Your account has been created. You may now log in.             │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 Login Flow

```text
User visits /login
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Username: [_______________]                                    │
│  [ Continue ]                                                    │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
System checks user's registered auth method
         │
         ├── PASSKEY/FIDO2 + available in browser
         │         │
         │         ▼
         │   WebAuthn authentication ceremony
         │   (Touch key, Face ID, fingerprint, etc.)
         │         │
         │         ▼
         │   Session Created ✓
         │
         ├── PASSKEY/FIDO2 + NOT available in browser
         │         │
         │         ▼
         │   ┌─────────────────────────────────────────────────┐
         │   │  Your passkey isn't available on this device.   │
         │   │                                                  │
         │   │  Send me a login link:                          │
         │   │    ○ Email: [_______________]                   │
         │   │    ○ SMS:   [_______________]                   │
         │   │                                                  │
         │   │  [ Send Link ]                                   │
         │   └─────────────────────────────────────────────────┘
         │         │
         │         ▼
         │   Magic link sent (15-min expiry)
         │   Click link → Session Created ✓
         │
         └── TOTP
                   │
                   ▼
             ┌─────────────────────────────────────────────────┐
             │  Enter your 6-digit code:                       │
             │                                                  │
             │  Code: [______]                                 │
             │                                                  │
             │  [ Verify ]                                      │
             └─────────────────────────────────────────────────┘
                   │
                   ▼
             TOTP verified → Session Created ✓
```

### 6.4 Magic Link Security

| Property | Value |
|----------|-------|
| Token length | 256 bits (cryptographically random) |
| Expiration | 15 minutes |
| Usage | Single-use (invalidated on first click) |
| Storage | Hash only (SHA-256) |
| Transport | Email or SMS (user's choice) |

---

## 7. Session Management

### 7.1 Session Properties

| Property | Value |
|----------|-------|
| Token format | 256-bit cryptographically random |
| Storage | `auth.db` sessions table (encrypted) |
| Duration | Indefinite (until logout or kicked) |
| Disconnect grace | 30 minutes |
| Concurrency | Single active session per user |

### 7.2 Session Lifecycle

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              SESSION STATES                                              │
└─────────────────────────────────────────────────────────────────────────────────────────┘

  Login successful
         │
         ▼
  ┌─────────────────────────────────────────────────────────────────────────────────────┐
  │  ACTIVE                                                                              │
  │  • User can browse, stream, track position                                           │
  │  • Heartbeat updates last_seen every 60 seconds                                      │
  └─────────────────────────────────────────────────────────────────────────────────────┘
         │
         │  Connection lost
         ▼
  ┌─────────────────────────────────────────────────────────────────────────────────────┐
  │  GRACE PERIOD (30 minutes)                                                           │
  │  • Session token still valid                                                         │
  │  • No heartbeat received                                                             │
  │  • User can reconnect without re-authenticating                                      │
  └─────────────────────────────────────────────────────────────────────────────────────┘
         │
         ├── Reconnects within 30 min → ACTIVE
         │
         └── 30 minutes expire
                   │
                   ▼
  ┌─────────────────────────────────────────────────────────────────────────────────────┐
  │  EXPIRED                                                                             │
  │  • Session invalid                                                                   │
  │  • User must log in again                                                           │
  └─────────────────────────────────────────────────────────────────────────────────────┘


  ┌─────────────────────────────────────────────────────────────────────────────────────┐
  │  FORCED TERMINATION                                                                  │
  │  Triggers:                                                                           │
  │  • User logs in from another device → This session dies immediately                 │
  │  • Admin revokes user → Session dies immediately                                    │
  │  • User clicks logout → Session dies immediately                                    │
  └─────────────────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Single Session Enforcement

When a user logs in:

1. Query for existing sessions for this user
2. Invalidate all existing sessions
3. Create new session
4. Return new session token

If an old session tries to use an invalidated token:

- Return 401 with message: "Session ended because you logged in elsewhere"

---

## 8. Authorization

### 8.1 Access Control Matrix

| Resource | Anonymous | Library User | User + Download | Admin (local) |
|----------|-----------|--------------|-----------------|---------------|
| `/auth/*` | ✓ | ✓ | ✓ | ✓ |
| `/library` | ✗ | ✓ | ✓ | ✓ |
| `/api/audiobooks` | ✗ | ✓ | ✓ | ✓ |
| `/api/stream/*` | ✗ | ✓ | ✓ | ✓ |
| `/api/position/*` (own) | ✗ | ✓ | ✓ | ✓ |
| `/api/position/*` (others) | ✗ | ✗ | ✗ | ✓ |
| `/api/download/*` | ✗ | ✗ | ✓ | ✓ |
| `/covers/*` | ✗ | ✓ | ✓ | ✓ |
| `/api/notifications` | ✗ | ✓ | ✓ | ✓ |
| `/api/contact` | ✗ | ✓ | ✓ | ✓ |
| `/utilities.html` | ✗ | ✗ | ✗ | ✓ |
| `/api/utilities/*` | ✗ | ✗ | ✗ | ✓ |
| `/api/system/*` | ✗ | ✗ | ✗ | ✓ |
| `/api/admin/*` | ✗ | ✗ | ✗ | ✓ |

### 8.2 Admin Endpoint Protection (v6.0+)

Admin endpoints (Back Office, service control, upgrades) are protected by the `admin_or_localhost` decorator at the application level, not the reverse proxy level. This ensures protection works regardless of deployment method:

```python
# In auth.py — the decorator adapts to deployment mode:
@admin_or_localhost
def admin_endpoint():
    # AUTH_ENABLED=true:  Requires authenticated admin user (401/403 otherwise)
    # AUTH_ENABLED=false: Requires localhost origin (404 otherwise)
    ...
```

**Applied to 9 endpoints** in `utilities_system.py`:

- `GET /api/system/services` — List services
- `POST /api/system/services/<name>/<action>` — Start/stop/restart
- `POST /api/system/services/start-all` — Start all services
- `POST /api/system/services/stop-all` — Stop all services
- `GET /api/system/services/<name>/status` — Service status
- `POST /api/system/upgrade` — Application upgrade
- `GET /api/system/upgrade/status` — Upgrade status
- `GET /api/system/diagnostics` — System diagnostics
- `GET /api/system/env` — Environment info

> **Note:** The previous design used Caddy-level URL blocking. The v6.0 approach moves protection into the application itself, making it deployment-agnostic — works with any reverse proxy (Caddy, nginx, Traefik) or direct access.

### 8.3 Download Permission

- Stored as `can_download` boolean on user record
- Checked on every download request
- Only allows opus files and metadata
- Never allows: AAXC sources, database files, config files

---

## 9. Data Model

### 9.1 Database Overview

| Database | Engine | Purpose | Encryption |
|----------|--------|---------|------------|
| `auth.db` | SQLCipher | Users, sessions, positions, notifications | AES-256 at rest |
| `audiobooks.db` | SQLite | Book metadata, genres, supplements | None (not sensitive) |

### 9.2 auth.db Schema

```sql
-- Users table
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    auth_type TEXT NOT NULL CHECK (auth_type IN ('passkey', 'fido2', 'totp')),
    auth_credential BLOB NOT NULL,
    can_download BOOLEAN DEFAULT FALSE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    -- Recovery options (user's choice to store or not)
    recovery_email TEXT,           -- Optional, stored encrypted in SQLCipher
    recovery_phone TEXT,           -- Optional, stored encrypted in SQLCipher
    recovery_enabled BOOLEAN DEFAULT FALSE,

    CHECK (length(username) >= 5 AND length(username) <= 16)
);

CREATE INDEX idx_users_username ON users(username);

-- Sessions table
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of session token
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,  -- NULL = no expiry (until logout/kick)
    user_agent TEXT,
    ip_address TEXT  -- For audit, not displayed to users
);

CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_token_hash ON sessions(token_hash);

-- User positions table
CREATE TABLE user_positions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id INTEGER NOT NULL,  -- References audiobooks.db
    position_ms INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, audiobook_id)
);

CREATE INDEX idx_user_positions_user_id ON user_positions(user_id);

-- Pending registrations table
CREATE TABLE pending_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of verification token
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_pending_token_hash ON pending_registrations(token_hash);

-- Notifications table
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('info', 'maintenance', 'outage', 'personal')),
    target_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL = all users
    starts_at TIMESTAMP,  -- NULL = immediately
    expires_at TIMESTAMP,  -- NULL = no expiry
    dismissable BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT DEFAULT 'admin'
);

CREATE INDEX idx_notifications_target ON notifications(target_user_id);
CREATE INDEX idx_notifications_active ON notifications(starts_at, expires_at);

-- Notification dismissals table
CREATE TABLE notification_dismissals (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (notification_id, user_id)
);

-- Inbox table (user messages to admin)
CREATE TABLE inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    reply_via TEXT NOT NULL CHECK (reply_via IN ('in-app', 'email')),
    reply_email TEXT,  -- Only if reply_via='email', deleted after reply
    status TEXT DEFAULT 'unread' CHECK (status IN ('unread', 'read', 'replied', 'archived')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    replied_at TIMESTAMP
);

CREATE INDEX idx_inbox_status ON inbox(status);

-- Contact log (audit trail, no content)
CREATE TABLE contact_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Backup codes table (for account recovery)
CREATE TABLE backup_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,      -- SHA-256 of the backup code
    used_at TIMESTAMP,            -- NULL if unused, timestamp when used
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_backup_codes_user_id ON backup_codes(user_id);
CREATE INDEX idx_backup_codes_hash ON backup_codes(code_hash);

-- Pending recovery requests (for magic link recovery)
CREATE TABLE pending_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,  -- SHA-256 of recovery token
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP                 -- NULL if unused
);

CREATE INDEX idx_pending_recovery_token ON pending_recovery(token_hash);
CREATE INDEX idx_pending_recovery_user ON pending_recovery(user_id);
```

### 9.3 Session Token Format

```text
Session token: 256-bit cryptographically random bytes
Encoded as: Base64URL (43 characters)
Storage: SHA-256 hash of token (not the token itself)

Example:
  Token (given to user): xK9mP2nQ7rS3tU8vW1xY4zA6bC0dE5fG2hI7jK4lM9n
  Stored (in database):  SHA256(token) = 3a7f8c9d...
```

---

## 10. API Design

### 10.1 Authentication Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /auth/register/start` | POST | Start registration (send verification) |
| `GET /auth/verify` | GET | Verify email/SMS link |
| `POST /auth/register/complete` | POST | Complete registration (set up auth method) |
| `POST /auth/login` | POST | Login with credentials |
| `POST /auth/magic-link` | POST | Request magic link |
| `GET /auth/magic-link/verify` | GET | Verify magic link |
| `POST /auth/logout` | POST | End session |

### 10.2 Library Endpoints (Existing, Auth-Protected)

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `GET /api/audiobooks` | GET | Required | List audiobooks |
| `GET /api/audiobooks/<id>` | GET | Required | Get audiobook details |
| `GET /api/stream/<id>` | GET | Required | Stream audio |
| `GET /api/collections` | GET | Required | List collections |
| `GET /covers/<file>` | GET | Required | Get cover image |

### 10.3 Position Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `GET /api/position/<id>` | GET | Required | Get position for audiobook |
| `PUT /api/position/<id>` | PUT | Required | Update position |
| `GET /api/positions` | GET | Required | Get all positions for user |

### 10.4 Download Endpoints

| Endpoint | Method | Auth | Permission | Description |
|----------|--------|------|------------|-------------|
| `GET /api/download/<id>` | GET | Required | can_download | Download opus file |
| `GET /api/download/<id>/metadata` | GET | Required | can_download | Download metadata JSON |

### 10.5 Notification Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `GET /api/notifications` | GET | Required | Get active notifications for user |
| `POST /api/notifications/<id>/dismiss` | POST | Required | Dismiss notification |

### 10.6 Contact Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `POST /api/contact` | POST | Required | Send message to admin |

### 10.7 Admin Endpoints (Localhost Only)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/admin/users` | GET | List users |
| `POST /api/admin/users` | POST | Create user |
| `PUT /api/admin/users/<id>` | PUT | Update user |
| `DELETE /api/admin/users/<id>` | DELETE | Delete user |
| `POST /api/admin/users/<id>/revoke` | POST | Revoke all sessions |
| `GET /api/admin/inbox` | GET | List messages |
| `POST /api/admin/inbox/<id>/reply` | POST | Reply to message |
| `POST /api/admin/notifications` | POST | Create notification |
| `DELETE /api/admin/notifications/<id>` | DELETE | Delete notification |

---

## 11. User Interface

### 11.1 Pages

| Page | URL | Access | Purpose |
|------|-----|--------|---------|
| Login | `/login` | Public | Username entry, auth flow |
| Register | `/register` | Public | Account creation |
| Library | `/library` | Authenticated | Browse audiobooks |
| Player | `/player/<id>` | Authenticated | Audio player |
| Contact | `/contact` | Authenticated | Message admin |
| Profile | `/profile` | Authenticated | View account, logout |

### 11.2 UI Components

#### 11.2.1 Notification Banner

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ ⓘ Library updated with 12 new titles!                                     [ Dismiss ]  │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ ⚠ Scheduled maintenance: Saturday 2am-4am EST                             [ Dismiss ]  │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ 🔴 Experiencing issues. Working on it.                                                  │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ 📬 Hey Bob! Just added that series you asked about. - Bosco               [ Dismiss ]  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

#### 11.2.2 User Menu

```text
┌─────────────────────┐
│  Bob            ▾   │
├─────────────────────┤
│  Profile            │
│  Contact Admin      │
│  ──────────────     │
│  Logout             │
└─────────────────────┘
```

#### 11.2.3 Session Expired Modal

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                         │
│                          Session Ended                                                  │
│                                                                                         │
│   Your session ended because you logged in from another device.                        │
│                                                                                         │
│                              [ Log In Again ]                                           │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 12. Notifications and Contact

### 12.1 Notification Types

| Type | Color | Dismissable | Use Case |
|------|-------|-------------|----------|
| `info` | Blue | Yes | Announcements, new content |
| `maintenance` | Yellow | Yes | Scheduled downtime |
| `outage` | Red | No | Unplanned issues |
| `personal` | Green | Yes | Direct message to user |

### 12.2 Contact Flow

```text
User submits message
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Store in inbox table                                            │
│  • from_user_id                                                  │
│  • message                                                       │
│  • reply_via (in-app or email)                                   │
│  • reply_email (if applicable, temporary)                        │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Send admin alert                                                │
│  • Email to bosco@thebosco.club                                 │
│  • SMS (optional): "New message from bob"                        │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
Admin reads via CLI: audiobook-inbox list
         │
         ▼
Admin replies via CLI: audiobook-inbox reply <id> "message"
         │
         ├── reply_via = 'in-app'
         │         │
         │         ▼
         │   Create personal notification for user
         │
         └── reply_via = 'email'
                   │
                   ▼
             Send email to user's reply_email
             Delete reply_email from database
```

### 12.3 Admin Alert Configuration

```bash
# /etc/audiobooks/admin.conf

ADMIN_EMAIL="bosco@thebosco.club"
ADMIN_SMS="+15551234567"  # Optional

ALERT_VIA_EMAIL=true
ALERT_VIA_SMS=true
ALERT_COOLDOWN_MINUTES=15
```

---

## 13. Backup and Recovery

### 13.1 Backup Strategy

| Method | Frequency | Retention | Purpose |
|--------|-----------|-----------|---------|
| BTRFS snapshots | Hourly | 24 hourly, 7 daily, 4 weekly | Quick rollback |
| SQLite online backup | Daily | 30 days | Consistent database copy |
| Litestream | Continuous | Point-in-time | Near-realtime recovery |

### 13.2 Backup Procedures

#### 13.2.1 SQLite Online Backup

```bash
# Safe backup while database is in use
sqlite3 audiobooks.db ".backup '/backup/audiobooks-$(date +%Y%m%d).db'"

# For SQLCipher (auth.db)
sqlcipher auth.db "PRAGMA key='<key>'; .backup '/backup/auth-$(date +%Y%m%d).db'"
```

#### 13.2.2 BTRFS Snapshots

```bash
# Create snapshot
sudo btrfs subvolume snapshot -r \
    /path/to/db-subvol \
    /path/to/db-subvol/.snapshots/$(date +%Y%m%d-%H%M%S)

# Restore from snapshot
sudo btrfs subvolume delete /path/to/db-subvol
sudo btrfs subvolume snapshot \
    /path/to/db-subvol/.snapshots/20260119-140000 \
    /path/to/db-subvol
```

### 13.3 Recovery Scenarios

| Scenario | Recovery Method |
|----------|-----------------|
| Accidental data deletion | Litestream point-in-time recovery |
| Database corruption today | BTRFS snapshot from this morning |
| Catastrophic failure | Daily backup from external storage |
| User wants position reset | Admin modifies user_positions table |

### 13.4 CLI Tools

```bash
# Create backup
$ audiobook-backup create
Creating backup...
  auth.db → /backup/audiobooks/2026-01-19/auth.db ✓
  audiobooks.db → /backup/audiobooks/2026-01-19/audiobooks.db ✓
Verifying integrity... OK
Backup complete.

# List backups
$ audiobook-backup list

# Restore from backup
$ audiobook-backup restore 2026-01-18
```

---

## 14. Operational Considerations

### 14.1 Logging

#### 14.1.1 What to Log

| Event | Log Level | Data Logged |
|-------|-----------|-------------|
| Login success | INFO | Username, timestamp, IP (hashed) |
| Login failure | WARN | Username, timestamp, IP (hashed), reason |
| Session created | INFO | Username, session ID prefix |
| Session terminated | INFO | Username, reason |
| Download | INFO | Username, audiobook ID |
| Admin action | INFO | Action type, target |

#### 14.1.2 What NOT to Log

- Email addresses
- Phone numbers
- Session tokens (full)
- TOTP codes
- IP addresses (unhashed)
- Message content

### 14.2 Monitoring

| Metric | Alert Threshold |
|--------|-----------------|
| Failed logins (per IP) | > 10 in 15 minutes |
| Failed logins (per user) | > 5 in 15 minutes |
| Active sessions | > 20 (unusual for 16-user base) |
| API error rate | > 5% |
| Response latency | > 2 seconds |

### 14.3 CLI Tools Summary

| Command | Purpose |
|---------|---------|
| `grotto-user` | User management (create, list, modify, delete) |
| `audiobook-session` | Session management (list, revoke) |
| `audiobook-notify` | Notification management |
| `audiobook-inbox` | Read and reply to user messages |
| `audiobook-backup` | Backup and restore |
| `audiobook-auth` | Test authentication methods |

---

## 15. Implementation Phases

### Phase 0: Foundation ✓

**Goal:** Infrastructure without breaking existing functionality

- [x] Caddy configuration
- [x] DNS setup (Cloudflare)
- [x] DDNS updater script
- [x] Security headers
- [x] Rate limiting
- [x] Verify existing app works through Caddy

**Deliverable:** HTTPS access to current app

---

### Phase 1: Auth Database & User Model ✓

**Goal:** Separate encrypted auth storage

- [x] SQLCipher integration
- [x] auth.db schema
- [x] User model implementation
- [x] Session model implementation
- [x] CLI: `grotto-user`
- [x] Unit tests

**Deliverable:** CLI user management

---

### Phase 2: Authentication Service ✓

**Goal:** Login/logout/session management

- [x] Auth service (integrated into port 5001)
- [x] TOTP registration/verification
- [ ] WebAuthn registration/verification (deferred to Phase 5)
- [x] Magic link generation/verification
- [x] Session creation/validation/invalidation
- [x] Single-session enforcement
- [x] Rate limiting
- [x] Integration tests

**Deliverable:** Functional authentication

---

### Phase 3: Library Service Integration ✓

**Goal:** Protect library endpoints with auth

- [x] Session middleware
- [x] User context injection
- [x] Per-user positions
- [x] Download permission enforcement
- [x] Audible sync disabled for non-admin
- [x] Back Office localhost restriction
- [x] Integration tests

**Deliverable:** Authenticated library access

---

### Phase 4: Public-Facing UI ✓

**Goal:** Login page, registration, auth flows

- [x] Login page (with TOTP and backup code recovery)
- [x] Registration page (multi-step with auth method selection)
- [x] Magic link landing page (verify.html)
- [x] TOTP setup (QR code)
- [x] Passkey setup flow (UI ready, marked "Coming Soon")
- [x] Session management UI (user menu in header)
- [x] Error pages (401.html, 403.html)
- [x] Mobile-responsive
- [x] Help tooltips for layperson users
- [x] Magic link email endpoint (SMTP config pending - Protonmail Bridge)

**Deliverable:** Complete auth UI

**Note:** Passkey/WebAuthn backend deferred to Phase 5. Magic link emails require
Protonmail Bridge setup (scripts/setup-email.sh).

---

### Phase 5: Notifications & Contact ✓

**Goal:** Admin-user communication

- [x] Notifications table and API
- [x] Notification display (banners)
- [x] Dismiss functionality
- [x] Contact form UI
- [x] Inbox table and CLI
- [x] Admin alerts (email/SMS)
- [x] Reply mechanism (in-app and email)
- [x] CLI: `audiobook-notify`, `audiobook-inbox`

**Deliverable:** Two-way communication

**Implementation Notes:**

- Notification types: info, maintenance, outage, personal
- Notifications shown as Art Deco styled banners on main library page
- Personal notifications target specific users
- Admin inbox stores user contact messages
- Admin can reply via in-app notification or email
- CLI tools for offline notification/inbox management
- Email alerts sent to admin when new contact messages arrive
- Reply emails use configured SMTP (Protonmail Bridge)

---

### Phase 6: Hardening & Audit ✓

**Goal:** Production-ready security

- [x] Security audit (bandit scan, code review)
- [x] Penetration testing (17 tests: token manipulation, SQL injection, session attacks)
- [x] Log sanitization verification (fixed 3 PII leaks)
- [x] Backup/restore testing (7 tests for encrypted DB recovery)
- [x] Failure mode documentation (AUTH_FAILURE_MODES.md - 12 scenarios)
- [x] Runbook (AUTH_RUNBOOK.md - operations guide)
- [x] Performance testing (13 benchmarks for latency and concurrency)

**Deliverable:** Confidence to go live

**Test Summary:**

- Security tests: 17 passed
- Backup tests: 7 passed
- Performance tests: 13 passed
- Total auth tests: 243 passed

---

### Phase 7: Go Live

**Goal:** Public access

- [ ] Open port 443 on router
- [ ] Activate Cloudflare DDNS
- [ ] Monitor logs (48 hours)
- [ ] Invite first test user
- [ ] Iterate on feedback

**Deliverable:** Live system

---

## 16. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | SQLCipher key management - where to store encryption key? | **Resolved**: Key stored in `auth.key` file, separate from database |
| 2 | Email/SMS provider for verification and magic links | **Partial**: Protonmail Bridge installed, requires interactive login (scripts/setup-email.sh) |
| 3 | TOTP recovery - what if user loses authenticator device? | **Resolved**: See Section 5.3 Recovery Model |
| 4 | Account deletion - user-initiated or admin-only? | Open |
| 5 | Position export - can users export their position data? | Open |

### Resolved: Recovery Model (Question #3)

Users have two recovery paths:

1. **Backup Codes** (always available)
   - 8 single-use codes generated at registration
   - Format: `XXXX-XXXX-XXXX-XXXX` (alphanumeric, no confusing chars)
   - User can regenerate codes when logged in (invalidates old codes)
   - Each code allows one account recovery (generates new TOTP + new backup codes)

2. **Magic Link Recovery** (only if user stored contact info)
   - User chooses at registration whether to store email/phone
   - If stored, can receive magic link for recovery
   - Magic link expires in 15 minutes

**Warning for users without stored contact:**
If a user loses their authenticator AND all backup codes, the account is unrecoverable. Admin can delete the account so they can re-register, but listening positions are lost.

---

## 17. Appendices

### Appendix A: Caddyfile Template

```text
library.thebosco.club {
    # TLS configuration (automatic via Let's Encrypt)

    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        X-XSS-Protection "1; mode=block"
        Content-Security-Policy "default-src 'self'"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    # Rate limiting
    rate_limit {
        zone login {
            key {remote_host}
            events 5
            window 15m
        }
    }

    # NOTE (v6.0+): Back Office / admin endpoint protection is handled
    # at the application level by the admin_or_localhost decorator,
    # not at the Caddy level. No path-based blocking needed here.

    # Auth + Library (single Flask API, port 5001)
    handle /auth/* {
        reverse_proxy localhost:5001
    }

    handle /api/* {
        reverse_proxy localhost:5001
    }

    # Static files
    handle {
        root * /opt/audiobooks/library/web-v2
        file_server
    }
}
```

### Appendix B: DDNS Update Script

```bash
#!/bin/bash
# /opt/audiobooks/scripts/ddns-update.sh

ZONE_ID="your-cloudflare-zone-id"
RECORD_ID="your-dns-record-id"
API_TOKEN="your-cloudflare-api-token"
DOMAIN="library.thebosco.club"

CURRENT_IP=$(curl -s https://api.ipify.org)
CACHED_IP=$(cat /var/lib/audiobooks/.cached_ip 2>/dev/null)

if [ "$CURRENT_IP" != "$CACHED_IP" ]; then
    curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
        -H "Authorization: Bearer $API_TOKEN" \
        -H "Content-Type: application/json" \
        --data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$CURRENT_IP\",\"ttl\":300}"

    echo "$CURRENT_IP" > /var/lib/audiobooks/.cached_ip
    logger "DDNS updated: $DOMAIN -> $CURRENT_IP"
fi
```

### Appendix C: Glossary

| Term | Definition |
|------|------------|
| **FIDO2** | Fast Identity Online 2, passwordless authentication standard |
| **Magic Link** | One-time URL sent via email/SMS for authentication |
| **Passkey** | WebAuthn credential stored on device (Face ID, fingerprint, etc.) |
| **SQLCipher** | SQLite extension providing transparent 256-bit AES encryption |
| **TOTP** | Time-based One-Time Password (6-digit codes from authenticator apps) |
| **WebAuthn** | Web Authentication API for passwordless authentication |

---

*End of Specification*
