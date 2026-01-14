# Intermittent Service Failures - Root Cause Analysis

**Date:** 2026-01-14
**Status:** RESOLVED
**Commit:** 2181198

---

## Executive Summary

Intermittent service failures (converter stopping mid-queue, mover leaving files in staging, services failing after reboot) were caused by **three interconnected issues**:

1. **Destructive Index Rebuilds** - The mover triggered full index rebuilds that cleared ASINs added by real-time updates
2. **Title Matching Failures** - Series-style audiobooks (e.g., "American Scandal") couldn't be matched due to filename/title mismatches
3. **Missing tmpfiles.d Configuration** - Required /tmp directories weren't being recreated on systems with tmpfs

---

## Symptoms Reported

- Conversion service stops with items still in queue
- Mover leaves files stranded in staging directory
- Services fail to start after reboot
- "Read-only file system" errors in journal
- Orphaned `--rebuild` processes accumulating
- Indexes become "stale" and don't reflect actual library contents

---

## Root Cause Analysis

### Issue #1: Destructive Index Rebuilds (PRIMARY)

**Location:** `scripts/move-staged-audiobooks` lines 172-176

**Problem:**
```bash
# OLD CODE - This was destroying the index!
flock -n "${AUDIOBOOKS_RUN_DIR}/queue-rebuild.lock" \
    "${AUDIOBOOKS_HOME}/scripts/build-conversion-queue" --rebuild >/dev/null 2>&1 &
```

After moving files, the mover triggered a full `--rebuild` which:
1. Cleared `converted_asins.idx` (`: > "$temp_file"`)
2. Tried to rebuild from chapters.json files (many missing)
3. Tried title matching (fails for series-style files)
4. **Lost all ASINs that were added by quick-update**

This created a cycle:
1. Converter finishes → quick-update adds ASIN ✓
2. Mover moves file → triggers rebuild
3. Rebuild clears index → tries to rediscover ASINs
4. Title matching fails → ASIN is lost
5. Next converter batch sees file as "needs conversion" again

**Fix:** Removed the rebuild trigger. The converter already updates indexes via `--quick-update` after each conversion.

---

### Issue #2: Title Matching Failures

**Location:** `scripts/build-conversion-queue` function `build_converted_asin_index()`

**Problem:**
For series-style audiobooks, the source filename differs from the library title:

| Source Filename | Library Title |
|-----------------|---------------|
| `American_Scandal_(Ad-free)_Bernie_Madoff__Sins_of_the_Father__1_(Ad-free)` | `Bernie Madoff \| Sins of the Father \| 1 (Ad-free)` |

After normalization:
- Source: `american scandal ad free bernie madoff sins of the father 1 ad free`
- Library: `bernie madoff sins of the father 1 ad free`

**These don't match** because AAXtoMP3 uses the actual audiobook metadata title, not the source filename which includes the series prefix.

**Fix:**
1. Rebuild now preserves existing entries (merges instead of clearing)
2. Added database sync as authoritative source of ASINs

---

### Issue #3: Missing tmpfs Configuration

**Location:** `systemd/audiobooks-tmpfiles.conf`

**Problem:**
The `/tmp/audiobook-triggers` directory was not in tmpfiles.d configuration. On systems with tmpfs for /tmp:
1. Reboot clears /tmp
2. Services start but can't create trigger files
3. "Read-only file system" or "No such file or directory" errors

**Fix:** Added to `audiobooks-tmpfiles.conf`:
```ini
d /tmp/audiobook-triggers 0755 audiobooks audiobooks -
```

---

### Issue #4: Missing chapters.json Files

**Root Cause:** Some conversions fail to extract cover art (FFmpeg error), which also skips chapters.json creation.

**Example Error:**
```
[out#0/image2 @ 0x55e124cebcc0] Output file does not contain any stream
Error opening output file .../Bernie Madoff | Sins of the Father | 1.jpg
```

When chapters.json is missing, the only way to discover the ASIN during rebuild is title matching, which fails for series-style files.

**Fix:** Database sync now provides ASINs even when chapters.json is missing.

---

## Fixes Applied

### Fix 1: Remove Destructive Rebuild Trigger

**File:** `scripts/move-staged-audiobooks`

```bash
# OLD (removed)
if [[ -x "${AUDIOBOOKS_HOME}/scripts/build-conversion-queue" ]]; then
    mkdir -p "${AUDIOBOOKS_RUN_DIR}"
    flock -n "${AUDIOBOOKS_RUN_DIR}/queue-rebuild.lock" \
        "${AUDIOBOOKS_HOME}/scripts/build-conversion-queue" --rebuild >/dev/null 2>&1 &
fi

# NEW (comment explaining why)
# NOTE: We no longer trigger --rebuild here because:
# 1. The converter already updates indexes via --quick-update after each conversion
# 2. Full rebuilds are destructive and clear ASINs that can't be rediscovered
# 3. This was causing orphaned rebuild processes to accumulate
```

### Fix 2: Preserve Existing Index Entries

**File:** `scripts/build-conversion-queue` function `build_converted_asin_index()`

```bash
# OLD
: > "$temp_file"  # This cleared everything!

# NEW
if [[ -f "$index_file" ]]; then
    cp "$index_file" "$temp_file"
    local preserved_count=$(wc -l < "$temp_file")
    log "  Preserved: $preserved_count existing entries"
else
    : > "$temp_file"
fi
```

### Fix 3: Add Database Sync

**File:** `scripts/build-conversion-queue` function `build_converted_asin_index()`

```bash
# Method 3: Sync from database (authoritative source of truth)
local db_count=0
if [[ -f "$db_path" ]] && command -v sqlite3 &>/dev/null; then
    log "  Syncing from database..."
    local db_asins
    db_asins=$(sqlite3 "$db_path" "SELECT DISTINCT asin FROM audiobooks WHERE asin IS NOT NULL AND asin <> ''" 2>/dev/null)
    if [[ -n "$db_asins" ]]; then
        echo "$db_asins" >> "$temp_file"
        db_count=$(echo "$db_asins" | wc -l)
    fi
    log "  From database: $db_count ASINs"
fi
```

### Fix 4: tmpfiles.d Configuration

**File:** `systemd/audiobooks-tmpfiles.conf`

```ini
# Triggers directory for inter-service signaling
d /tmp/audiobook-triggers 0755 audiobooks audiobooks -
```

---

## Verification

After fixes, rebuild output shows:

```
[15:39:44]   Preserved: 1203 existing entries      ← Fix #2 working
[15:39:45]   From chapters.json: 1136 entries      ← Existing method
[15:40:00]   Backfilled: 1117 ASINs from title matching ← Existing method
[15:40:00]   Syncing from database...
[15:40:00]   From database: 1723 ASINs             ← Fix #3 working
[15:40:00] Converted ASIN index: 1739 unique ASINs (merged from all sources)
```

Queue dropped from **52 stuck items** to **13 actual unconverted files**.

---

## Prevention Measures

1. **Real-time updates via quick-update**: Each conversion immediately updates indexes
2. **Merge-based rebuilds**: Full rebuilds preserve existing entries
3. **Database as source of truth**: ASINs from database can't be lost
4. **tmpfiles.d for tmpfs systems**: Required directories recreated at boot
5. **Documentation**: All tmpfs considerations documented in README, INSTALL, ARCHITECTURE, QUICKSTART

---

## Files Modified

| File | Change |
|------|--------|
| `scripts/move-staged-audiobooks` | Removed destructive --rebuild trigger |
| `scripts/build-conversion-queue` | Added preserve + database sync |
| `systemd/audiobooks-tmpfiles.conf` | Added /tmp/audiobook-triggers |
| `README.md` | tmpfs documentation |
| `library/INSTALL.md` | tmpfs documentation |
| `docs/ARCHITECTURE.md` | tmpfs documentation |
| `library/QUICKSTART.md` | tmpfs troubleshooting |
| `systemd/*.service` (5 files) | RequiresMountsFor=/opt/audiobooks |

---

## Related Issues

- Orphaned `--rebuild` processes: Resolved by removing mover's rebuild trigger
- Permission denied on lock files: Non-critical (only affects manual testing as non-audiobooks user)
- upgrade.sh race condition: Still present but lower priority (only affects upgrade scenarios)
