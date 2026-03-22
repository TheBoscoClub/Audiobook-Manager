# Upgrade System Consistency — Mandatory Cross-File Review

Any change to upgrade functionality in ANY of these files requires review
and update of ALL of them:

| File | Role |
|------|------|
| upgrade.sh | CLI upgrade engine |
| scripts/upgrade-helper-process | Privileged bridge for web upgrades |
| library/backend/api_modular/utilities_system.py | API endpoints |
| library/web-v2/utilities.html | Upgrade UI markup |
| library/web-v2/js/utilities.js | Upgrade UI logic |
| install.sh | First-time installation |
| caddy/audiobooks.conf | Caddy maintenance routing |
| caddy/maintenance.html | External maintenance page |

## Canonical Service Names

ALL audiobook systemd services use singular `audiobook-*` (never `audiobooks-*`).
See systemd/*.service for authoritative list. Any code using `audiobooks-` (plural)
is a bug.

## Upgrade Feature Parity

Every upgrade option available in upgrade.sh CLI MUST also be available in the
web UI. No gatekeeping. If a new flag is added to upgrade.sh, the corresponding
UI control, API field, and helper parsing MUST be added in the same commit or PR.
