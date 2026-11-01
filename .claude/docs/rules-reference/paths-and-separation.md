# No Hardcoded Paths & Project/App Separation — reference (NOT auto-loaded)

Detail moved out of `.claude/rules/paths-and-separation.md`, same headings. Verbatim; the rule file keeps the binding text.

## No Hardcoded Paths — EVER

- Why: users set paths in `/etc/audiobooks/audiobooks.conf`; literals break customization and fail silently

## Complete Separation of Project and Application

**Project (dev):** `<your-projects-dir>/Audiobook-Manager/` (git repo); `./library/testdata/` (synthetic test data, NOT production); `./library/backend/audiobooks-dev.db` (dev DB, 64KB, 5 test records); `./config.env` (dev paths within project, ports 9090/6001 vs production 8443/5001).
